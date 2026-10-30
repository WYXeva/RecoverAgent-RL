"""Versioned Full732 A-only adapter; immutable inventory, no validation split."""

import gzip
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .demonstrations import sha256
from .io import digest, files_manifest, read_json, write_json
from .teacher_inventory import record_line

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "96265f67a5828b101f5db32a804008478d7c419a01e079f6e468b8e5f164735f"
ARCHIVE = "64c2a5854f0a3a9cfab72f71e13682dce5e535ff0b885768b98dc0073d6a7a48"
SPLIT = "0466cec29d9ad770a1cfeacb4c93945e5fcbcad68b9e980c5264d447418121a2"
FORMAT = "full732-base-sft-m1-v1"
LOSS = "trajectory_turn_balanced_sequence_mean"


def json_rows(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def verify_roles(plan, inventory):
    rows = plan["rows"]
    roles = {r["task_id"]: r for r in rows}
    expected = {"base_sft": 244, "recovery_grpo": 244, "shared_validation": 244}
    if (
        len(rows) != 732
        or len(roles) != 732
        or Counter(r["role"] for r in rows) != expected
    ):
        raise ValueError("Frozen A/B/C coverage differs")
    if len({r["task_id"] for r in inventory}) != len(inventory):
        raise ValueError("Duplicate source task")
    for row in inventory:
        member = roles.get(row["task_id"], {})
        if member.get("role") != "base_sft" or any(
            member.get(k) != row[k] for k in ("scenario_id", "official_split")
        ):
            raise ValueError("Training source is outside frozen A244")
    accepted = [r for r in inventory if r["disposition"] != "DEFERRED"]
    if (
        Counter(r["disposition"] for r in inventory)
        != {"NATIVE": 130, "COMPACT_DERIVED": 8, "DEFERRED": 6}
        or len({r["scenario_id"] for r in accepted}) != 138
    ):
        raise ValueError("Frozen accepted trajectory set differs")
    return accepted


def weighted_rows(rows, accepted):
    expected = {r["trajectory_id"]: r for r in accepted}
    sizes = {
        tid: r["compact" if r["disposition"] == "COMPACT_DERIVED" else "native"][
            "assistant_samples"
        ]
        for tid, r in expected.items()
    }
    total = sum(sizes.values())
    if not sizes or min(sizes.values()) <= 0:
        raise ValueError("Empty accepted trajectory")
    seen, counts, mass = set(), Counter(), defaultdict(float)
    for row in rows:
        tid = row["trajectory_id"]
        source = expected.get(tid)
        if source is None or any(
            row[k] != source[k]
            for k in ("task_id", "scenario_id", "source_demo_sha256")
        ):
            raise ValueError("Unexpected training example provenance")
        key = (tid, row["assistant_index"])
        if key in seen:
            raise ValueError("Duplicated Assistant target")
        seen.add(key)
        ids, start = row["input_ids"], row["target_start"]
        if (
            not 0 < start < len(ids) <= 32768
            or not len(ids) - start <= 4096
            or (
                row["labels"] != [-100] * start + ids[start:]
                or row["loss_mask"] != [0] * start + [1] * (len(ids) - start)
                or row["attention_mask"] != [1] * len(ids)
            )
        ):
            raise ValueError("Assistant-only loss mask or length differs")
        weight = total / (len(sizes) * sizes[tid])
        counts[tid] += 1
        mass[tid] += weight
        yield dict(row, example_weight=weight)
    if counts != sizes or any(
        not math.isclose(value, total / len(sizes), rel_tol=1e-12)
        for value in mass.values()
    ):
        raise ValueError("Missing trajectory targets or unbalanced weights")


def prepare(dataset, archive, output):
    dataset, archive, output = Path(dataset), Path(archive), Path(output)
    if (
        output.resolve() == dataset.resolve()
        or dataset.resolve() in output.resolve().parents
    ):
        raise ValueError("Output must not modify the original data package")
    if sha256(dataset / "manifest.json") != MANIFEST or sha256(archive) != ARCHIVE:
        raise ValueError("Trusted source manifest/archive differs")
    split_path = ROOT / "configs/unified_task_split_full_pool_candidate.json"
    if sha256(split_path) != SPLIT:
        raise ValueError("Frozen Full732 split changed")
    inventory = read_json(dataset / "inventory.json")
    accepted = verify_roles(read_json(split_path), inventory)
    output.mkdir(parents=True, exist_ok=False)
    # The sealed verifier independently re-renders targets with their then-visible
    # schemas. It verifies every package file and its four source-code hashes.
    command = [
        sys.executable,
        str(ROOT / "scripts/verify_teacher_inventory.py"),
        str(dataset.resolve()),
        "--expected-manifest-sha256",
        MANIFEST,
        "--retokenize",
    ]
    with (output / "retokenization.log").open("x") as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise ValueError("Independent retokenization failed; inspect retained log")
    receipt = json.loads((output / "retokenization.log").read_text().splitlines()[-1])
    if (
        receipt["status"] != "PASS"
        or receipt["retokenized"] is not True
        or receipt["counts"]
        != {
            "assistant_samples": 2070,
            "supervised_tokens": 338413,
            "masked_tokens": 22444500,
            "accepted_tokenized_candidates": 138,
        }
    ):
        raise ValueError("Retokenization coverage differs")
    counts, mass, longest = Counter(), defaultdict(float), 0
    with gzip.open(output / "train.jsonl.gz", "xt", encoding="utf-8") as stream:
        for row in weighted_rows(
            json_rows(dataset / "assistant_examples.jsonl.gz"), accepted
        ):
            stream.write(record_line(row) + "\n")
            counts.update(samples=1, supervised_tokens=sum(row["loss_mask"]))
            mass[row["trajectory_id"]] += row["example_weight"]
            longest = max(longest, len(row["input_ids"]))
    if counts != {"samples": 2070, "supervised_tokens": 338413} or longest != 32693:
        raise ValueError("Derived counts differ")
    write_json(output / "provenance.json", accepted)
    manifest = {
        "format": FORMAT,
        "scope": "custom_research_frozen_A244",
        "source_manifest_sha256": MANIFEST,
        "source_archive_sha256": ARCHIVE,
        "split_file_sha256": SPLIT,
        "accepted_task_ids": [r["task_id"] for r in accepted],
        "accepted_task_ids_sha256": digest(sorted(r["task_id"] for r in accepted)),
        "counts": dict(counts),
        "trajectories": len(accepted),
        "longest_sequence": longest,
        "representations": {"native": 130, "compact_derived": 8},
        "loss_contract": LOSS,
        "weight_formula": "w_i = N / (T * n_trajectory); N=2070, T=138; mean(w)=1",
        "trajectory_weight_min": min(mass.values()),
        "trajectory_weight_max": max(mass.values()),
        "retokenization": receipt,
        "training_validation": None,
        "C244_in_training": False,
        "training_started": False,
        "files": files_manifest(output),
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def verify_prepared(dataset, expected_sha):
    dataset = Path(dataset)
    if sha256(dataset / "manifest.json") != expected_sha:
        raise ValueError("Prepared data manifest changed")
    manifest = read_json(dataset / "manifest.json")
    files = files_manifest(dataset)
    files.pop("manifest.json")
    if (
        files != manifest["files"]
        or any(
            manifest[k] != v
            for k, v in {
                "format": FORMAT,
                "source_manifest_sha256": MANIFEST,
                "source_archive_sha256": ARCHIVE,
                "split_file_sha256": SPLIT,
                "training_validation": None,
                "C244_in_training": False,
                "loss_contract": LOSS,
            }.items()
        )
        or manifest["retokenization"]["retokenized"] is not True
    ):
        raise ValueError("Prepared data contract differs")
    return manifest
