"""Immutable raw-demo reuse and suffix-only encoding for the selected v2 model."""

import copy
import json
from pathlib import Path

from . import APPWORLD_REVISION
from .base_sft import assistant_examples
from .demonstrations import as_demo, filter_attempt, sha256
from .full732_sft_data import MANIFEST as MANIFEST_SHA
from .io import digest, read_json, write_json
from .schema_compatibility import convert_catalogue, load_public_openapi
from .v2_tasks import context_for, rank
from .v2_context import sequence_limit


def write_rows(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def encode(sample, tokenizer, *, max_length=None):
    max_length = sequence_limit() if max_length is None else max_length
    rows = assistant_examples(sample, tokenizer, max_length=max_length)
    if not rows or any(sum(r["loss_mask"]) > 4096 for r in rows):
        raise ValueError("Target too long or empty; refusing silent truncation")
    boundary = sample.get("recovery", {}).get("suffix_start", 0)
    for row in rows:
        if row["assistant_index"] < boundary or any(
            x != -100 for x in row["labels"][: row["target_start"]]
        ):
            raise ValueError("Prefix/control token supervision")
        row["v2_context"] = context_for(row["task_id"], training=True)
        row["example_weight"] = 1.0
    return rows


def prepare_base(dataset, output, tokenizer, identity, target=96, *, max_length=None):
    max_length = sequence_limit() if max_length is None else max_length
    dataset, output = Path(dataset), Path(output)
    if sha256(dataset / "manifest.json") != MANIFEST_SHA:
        raise ValueError("Original Teacher manifest changed")
    manifest = read_json(dataset / "manifest.json")
    for name, expected in manifest["files"].items():
        if sha256(dataset / name) != expected:
            raise ValueError("Original Teacher file changed: " + name)
    samples = []
    for sample in read_rows(dataset / "successes.jsonl"):
        try:
            context_for(sample["task_id"], training=True)
        except ValueError:
            continue
        samples.append(sample)
    samples.sort(key=lambda r: rank(r["task_id"]))
    # Convert only public Schemas. Raw messages/arguments remain byte-for-byte values.
    catalogue = {}
    for sample in samples:
        for turn in sample["turns"]:
            for tool in turn["tools"]:
                name = tool["function"]["name"]
                if name in catalogue and catalogue[name] != tool:
                    raise ValueError("Historical public Schema differs within dataset")
                catalogue[name] = tool
    documents, source = load_public_openapi(list(catalogue.values()))
    audit = convert_catalogue(
        list(catalogue.values()), documents, appworld_revision=APPWORLD_REVISION
    )
    converted = {t["function"]["name"]: t for t in audit["compatible_catalogue"]}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "schema_conversion.json", {**audit, "source": source})
    rows, accepted, deferred = [], [], []
    for raw in samples:
        if len(accepted) >= target:
            break
        sample = copy.deepcopy(raw)
        for turn in sample["turns"]:
            turn["tools"] = [converted[t["function"]["name"]] for t in turn["tools"]]
        try:
            encoded = encode(sample, tokenizer, max_length=max_length)
        except ValueError as exc:
            if "too long" not in str(exc):
                raise
            deferred.append({"task_id": sample["task_id"], "reason": str(exc)})
            continue
        accepted.append(sample)
        for row in encoded:
            row["source_demo_sha256"] = digest(raw)
        rows.extend(encoded)
    if not rows:
        raise ValueError("No complete train-only examples")
    write_rows(output / "samples.jsonl", accepted)
    write_rows(output / "examples.jsonl", rows)
    report = {
        "model": identity,
        "source_manifest_sha256": MANIFEST_SHA,
        "train_side_original_successes": len(samples),
        "accepted": len(accepted),
        "deferred": deferred,
        "examples": len(rows),
        "supervised_tokens": sum(sum(r["loss_mask"]) for r in rows),
        "max_sequence_tokens": max(len(r["input_ids"]) for r in rows),
        "sequence_limit": max_length,
        "examples_sha256": sha256(output / "examples.jsonl"),
        "no_truncation": True,
        "training_started": False,
    }
    write_json(output / "preparation.json", report)
    return report


def prepare_recovery(directory, tokenizer, *, max_length=None):
    """Use the existing real receipt verifier, not reward alone, for acceptance."""
    from .state import inspect_snapshot

    max_length = sequence_limit() if max_length is None else max_length
    directory = Path(directory)
    record = read_json(directory / "trajectory.json")
    context_for(record["task_id"], training=True)
    evaluation = (
        read_json(directory / "evaluation.json")
        if (directory / "evaluation.json").exists()
        else None
    )
    reasons = filter_attempt(
        record,
        evaluation,
        {record["task_id"]},
        {"repeated_identical_call_limit": 3},
        directory,
    )
    result = {
        "status": "rejected",
        "reasons": reasons,
        "examples": 0,
        "supervised_tokens": 0,
        "sequence_limit": max_length,
    }
    if not reasons:
        source, context, _ = inspect_snapshot(record["recovery"]["snapshot_ref"])
        if (
            source["checkpoint_id"] != record["recovery"]["checkpoint_id"]
            or context["messages"]
            != record["messages"][: record["recovery"]["snapshot_prefix_end"]]
        ):
            raise ValueError("Recovery prefix/source changed")
        sample = as_demo(record)
        write_json(directory / "recovery_sample.json", sample)
        try:
            rows = encode(sample, tokenizer, max_length=max_length)
        except ValueError as exc:
            if "too long" not in str(exc):
                raise
            result.update(status="deferred", reasons=[str(exc)])
        else:
            write_rows(directory / "examples.jsonl", rows)
            result.update(
                status="eligible",
                examples=len(rows),
                supervised_tokens=sum(sum(r["loss_mask"]) for r in rows),
            )
    write_json(directory / "suffix_acceptance.json", result)
    return result


def mix_recovery(recovery, normal):
    """One pass over real suffixes; token-weighted 50/50 loss, no fake duplicates."""
    if not recovery or not normal:
        raise ValueError("Recovery mix needs both real suffix and normal examples")
    # Select complete normal trajectories close to the suffix token count. The
    # residual weighting balances tokens without duplicating a short suffix.
    target = sum(sum(r["loss_mask"]) for r in recovery)
    grouped = {}
    for row in normal:
        context_for(row["task_id"], training=True)
        grouped.setdefault(row.get("trajectory_id", row["task_id"]), []).append(row)
    selected, count = [], 0
    while grouped:
        key = min(
            grouped,
            key=lambda k: (
                abs(target - count - sum(sum(r["loss_mask"]) for r in grouped[k])),
                rank(k),
            ),
        )
        tokens = sum(sum(r["loss_mask"]) for r in grouped[key])
        if selected and abs(target - count - tokens) >= abs(target - count):
            break
        selected.extend(grouped.pop(key))
        count += tokens
    normal = selected
    result = []
    total_examples = len(recovery) + len(normal)
    for kind, rows in [("recovery", recovery), ("normal", normal)]:
        tokens = sum(sum(r["loss_mask"]) for r in rows)
        for row in rows:
            context_for(row["task_id"], training=True)
            result.append(
                {
                    **row,
                    "mix_kind": kind,
                    "example_weight": 0.5
                    * total_examples
                    * sum(row["loss_mask"])
                    / tokens,
                }
            )
    return result
