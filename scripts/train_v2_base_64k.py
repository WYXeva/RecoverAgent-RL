#!/usr/bin/env python3
"""Train only Base-SFT from the completed 256-task pool, using the v2 trainer."""

import argparse
import gzip
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import traceback

from recoveragent.demonstrations import sha256, validate_demo
from recoveragent.io import read_json, write_json
from recoveragent.v2_data import encode, write_rows
from recoveragent.v2_tasks import ROOT, context_for

CONFIG = ROOT / "configs/v2_base_sft_64k_20261112.json"


def verify_source(cfg):
    lock_path = ROOT / cfg["source_lock"]
    lock = read_json(lock_path)
    required = {
        str(p.relative_to(ROOT)) for p in (ROOT / "src/recoveragent").glob("*.py")
    }
    required |= {str(CONFIG.relative_to(ROOT)), "scripts/train_v2_base_64k.py"}
    if (
        lock["runtime_revision"] != cfg["format"]
        or not required <= lock["files"].keys()
    ):
        raise ValueError("Incomplete Base-SFT source lock")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("Base-SFT source changed: " + name)
    return sha256(lock_path)


def prepare(package, output, cfg, tokenizer):
    package, output = Path(package), Path(output)
    expected = cfg["dataset"]
    manifest = read_json(package / "manifest.json")
    if sha256(package / "manifest.json") != expected["manifest_sha256"]:
        raise ValueError("Teacher pool manifest changed")
    for name, checksum in manifest["files"].items():
        if Path(name).name != name or sha256(package / name) != checksum:
            raise ValueError("Teacher pool file changed")
    samples = [
        json.loads(line)
        for line in (package / "successes.jsonl").read_text().splitlines()
    ]
    sources = {r["task_id"]: r for r in manifest["sources"]}
    if (
        len(samples) != expected["tasks"]
        or len(sources) != len(samples)
        or len({s["task_id"] for s in samples}) != len(samples)
    ):
        raise ValueError("Teacher task count or uniqueness mismatch")
    output.mkdir(parents=True, exist_ok=False)
    total_examples, tokens, maximum = 0, 0, 0
    audit = []
    with (output / "examples.jsonl").open("x") as stream:
        for sample in samples:
            task = sample["task_id"]
            context_for(task, training=True)
            validate_demo(sample)
            source = package / "samples" / task
            receipt = read_json(source / "encoding.json")
            if (
                sha256(source / "sample.json") != sources[task]["sha256"]
                or receipt["sample_sha256"] != sources[task]["sha256"]
                or read_json(source / "sample.json") != sample
                or not receipt["eligible"]
                or sha256(source / "examples.jsonl.gz") != receipt["examples_sha256"]
            ):
                raise ValueError("Source sample or encoding checksum mismatch: " + task)
            rows = encode(
                sample, tokenizer, max_length=cfg["student_limits"]["max_input_tokens"]
            )
            with gzip.open(source / "examples.jsonl.gz", "rt") as old:
                previous = [json.loads(line) for line in old]
            if rows != previous:
                raise ValueError(
                    "Remote retokenization differs from accepted encoding: " + task
                )
            supervised = sum(sum(r["loss_mask"]) for r in rows)
            longest = max(len(r["input_ids"]) for r in rows)
            if (len(rows), supervised, longest) != (
                receipt["examples"],
                receipt["supervised_tokens"],
                receipt["max_sequence_tokens"],
            ):
                raise ValueError("Encoding statistics changed: " + task)
            for row in rows:
                stream.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
            total_examples += len(rows)
            tokens += supervised
            maximum = max(maximum, longest)
            audit.append(
                {
                    "task_id": task,
                    "examples": len(rows),
                    "supervised_tokens": supervised,
                    "source_sha256": sources[task]["sha256"],
                    "encoding_sha256": receipt["examples_sha256"],
                }
            )
    if (total_examples, tokens, maximum) != (
        expected["examples"],
        expected["supervised_tokens"],
        expected["max_sequence_tokens"],
    ):
        raise ValueError("Final training dataset counts changed")
    write_rows(output / "samples.jsonl", samples)
    report = {
        "model": cfg["models"][cfg["selected_model"]],
        "source_manifest_sha256": expected["manifest_sha256"],
        "accepted": len(samples),
        "examples": total_examples,
        "supervised_tokens": tokens,
        "max_sequence_tokens": maximum,
        "sequence_limit": cfg["student_limits"]["max_input_tokens"],
        "examples_sha256": sha256(output / "examples.jsonl"),
        "remote_retokenization_exact": True,
        "no_truncation": True,
        "sources": audit,
    }
    write_json(output / "preparation.json", report)
    return report


def validate_stage(receipt, profile, data, cfg):
    expected_steps = {"short": 2, "long": 1}.get(profile)
    if expected_steps is None:
        expected_steps = math.ceil(
            data["examples"] / cfg["base_sft"]["gradient_accumulation_steps"]
        )
    if (
        receipt["profile"] != profile
        or receipt["stage"] != "S0_base"
        or receipt["parent"] is not None
        or not receipt["reload_passed"]
        or receipt["adapter_sha256_before"] == receipt["adapter_sha256_after"]
        or receipt["data_sha256"] != data["examples_sha256"]
        or receipt["optimizer_steps"] != expected_steps
        or receipt["sequence_limit"] != 65536
    ):
        raise ValueError("Base-SFT stage acceptance failed: " + profile)


def run(package, output, cfg, deadline_epoch):
    import torch
    from recoveragent.v2_model import load_tokenizer, verify_weights
    from recoveragent.v2_training import verify_checkpoint

    if (
        not torch.cuda.is_available()
        or torch.cuda.device_count() != 1
        or not torch.cuda.is_bf16_supported()
    ):
        raise ValueError("One BF16 GPU required")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    identity = cfg["models"][cfg["selected_model"]]
    started = time.time()
    summary = {
        "status": "started",
        "training_only": "base_sft",
        "stages": {},
        "started_epoch": started,
        "deadline_epoch": deadline_epoch,
        "source_lock_sha256": verify_source(cfg),
    }
    write_json(output / "run_config.json", cfg)
    write_json(
        output / "preflight.json",
        {
            "gpu": torch.cuda.get_device_name(0),
            "memory_bytes": torch.cuda.get_device_properties(0).total_memory,
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "weights": verify_weights(identity),
            **summary,
        },
    )
    try:
        prepared = prepare(package, output / "data", cfg, load_tokenizer(identity))
        summary["data"] = {k: v for k, v in prepared.items() if k != "sources"}
        for profile, name in (
            ("short", "smoke_short"),
            ("long", "smoke_long"),
            ("formal", "S0_base"),
        ):
            remaining = deadline_epoch - time.time()
            if remaining <= 0:
                raise TimeoutError("Base-SFT job deadline")
            # Each stage loads a fresh pinned base. Smoke updates never leak into S0.
            with (output / (name + ".log")).open("x") as log:
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "stage",
                        "--data",
                        str(output / "data/examples.jsonl"),
                        "--output",
                        str(output / name),
                        "--profile",
                        profile,
                        "--deadline-epoch",
                        str(deadline_epoch),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=remaining + 120,
                )
            receipt = verify_checkpoint(output / name, identity)
            validate_stage(receipt, profile, prepared, cfg)
            summary["stages"][name] = receipt
            write_json(output / (name + "_accepted.json"), receipt)
        summary["status"] = "base_sft_complete"
    except BaseException as exc:
        summary.update(status="stopped", error_type=type(exc).__name__)
        (output / "failure.log").write_text(traceback.format_exc())
        raise
    finally:
        summary["wall_seconds"] = time.time() - started
        write_json(output / "summary.json", summary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "stage"])
    parser.add_argument("--package")
    parser.add_argument("--data")
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", choices=["short", "long", "formal"])
    parser.add_argument("--deadline-epoch", required=True, type=float)
    args = parser.parse_args()
    cfg = read_json(CONFIG)
    verify_source(cfg)
    if args.command == "run":
        run(args.package, args.output, cfg, args.deadline_epoch)
    else:
        from recoveragent.v2_training import train_sft

        train_sft(
            cfg,
            cfg["models"][cfg["selected_model"]],
            args.data,
            args.output,
            profile=args.profile,
            deadline=time.monotonic() + max(0, args.deadline_epoch - time.time()),
        )


if __name__ == "__main__":
    main()
