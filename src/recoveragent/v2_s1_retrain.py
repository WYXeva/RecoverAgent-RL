"""Train the qualified stopped campaign from S0, then run unchanged dev24."""

import argparse
import gzip
import json
from pathlib import Path
import time

from .demonstrations import sha256
from .io import read_json, write_json
from .teacher_escalation import train_from_s0
from .v2_data import mix_recovery, write_rows
from .v2_runtime import S1_RETRAIN_RUNTIME as RUNTIME, S1_RUNTIME, verify_lock
from .v2_tasks import ROOT, context_for, plan


def configuration():
    verify_lock(RUNTIME)
    cfg = read_json(ROOT / f"configs/{RUNTIME}.json")
    original = read_json(ROOT / f"configs/{S1_RUNTIME}.json")
    if (
        cfg["dev_task_ids"] != plan()["dev24"]
        or cfg["student_limits"] != original["student_limits"]
        or cfg["models"] != original["models"]
        or cfg["s0"] != original["s0"]
        or cfg["environment_contract"] != original["environment_contract"]
        or cfg["execution_protocol"] != RUNTIME
    ):
        raise ValueError("Retraining identity or paired evaluation contract changed")
    return cfg


def load_groups(root, manifest, cfg):
    groups, seen = {}, set()
    for kind in ("recovery_suffix", "fresh_full", "original_replay"):
        entry = manifest["groups"][kind]
        name = Path(entry["file"])
        if name.is_absolute() or ".." in name.parts:
            raise ValueError("Invalid training artifact path")
        path = root / name
        if sha256(path) != entry["sha256"]:
            raise ValueError("Training source changed: " + kind)
        with gzip.open(path, "rt") as stream:
            rows = [json.loads(line) for line in stream]
        tasks = {r["task_id"] for r in rows}
        expected = cfg["data_groups"][kind]
        if (
            len(rows) != expected["examples"]
            or sorted(tasks) != expected["task_ids"]
            or sum(sum(r["loss_mask"]) for r in rows) != expected["supervised_tokens"]
            or seen & tasks
        ):
            raise ValueError("Training group membership/count changed: " + kind)
        for task in tasks:
            context_for(task, training=True)
        seen.update(tasks)
        groups[kind] = [{**r, "teacher_data_kind": kind} for r in rows]
    return groups


def prepare(output, deployment, cfg):
    from .v2_training import validate_training_rows

    root = Path(deployment["data_sources"])
    source = root / "manifest.json"
    if sha256(source) != cfg["data_manifest_sha256"]:
        raise ValueError("Frozen qualified data manifest changed")
    manifest = read_json(source)
    if manifest["student"] != cfg["models"][cfg["selected_model"]]:
        raise ValueError("Training source tokenizer/model mismatch")
    groups = load_groups(root, manifest, cfg)
    rows = mix_recovery(
        groups["recovery_suffix"],
        groups["fresh_full"] + groups["original_replay"],
        recovery_fraction=cfg["recovery_sft"]["recovery_supervised_token_fraction"],
        select_normal=False,
    )
    validate_training_rows(rows, cfg["student_limits"]["max_input_tokens"])
    target = output / "training_data"
    target.mkdir(parents=True, exist_ok=False)
    write_rows(target / "examples.jsonl", rows)
    write_json(target / "sources.json", manifest)
    write_json(
        target / "manifest.json",
        {
            "revision": RUNTIME,
            "expected_parent": cfg["s0"],
            "source_lock_sha256": verify_lock(RUNTIME)["lock_sha256"],
            "data_manifest_sha256": cfg["data_manifest_sha256"],
            "examples_sha256": sha256(target / "examples.jsonl"),
            "examples": len(rows),
            "max_sequence_tokens": max(len(r["input_ids"]) for r in rows),
            "groups": {
                kind: {
                    **cfg["data_groups"][kind],
                    "objective_fraction": sum(
                        r["example_weight"]
                        for r in rows
                        if r["teacher_data_kind"] == kind
                    )
                    / len(rows),
                }
                for kind in groups
            },
            "truncated": False,
            "validation_data_used": False,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=["prepare", "train", "train-worker", "evaluation"]
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--deployment", type=Path)
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    cfg = configuration()
    if args.stage == "train-worker":
        from .v2_training import train_sft

        payload = read_json(args.input)
        if payload.pop("config") != cfg:
            raise ValueError("Retraining worker config changed")
        payload["deadline"] = time.monotonic() + max(
            0, payload.pop("deadline_epoch") - time.time()
        )
        train_sft(cfg, **payload)
        return
    from .v2_s1_batch import initialize, feedback, checkpoint

    output = args.output.resolve()
    deployment = read_json(args.deployment)
    initialize(output, cfg, deployment)
    if args.stage == "prepare":
        prepare(output, deployment, cfg)
    elif args.stage == "train":
        train_from_s0(
            output,
            deployment,
            cfg,
            revision=RUNTIME,
            source_lock_sha256=verify_lock(RUNTIME)["lock_sha256"],
            worker_script=ROOT / "scripts/retrain_v2_s1.py",
        )
    else:
        saved = checkpoint(deployment["s1"], cfg)
        accepted = read_json(output / (cfg["output_name"] + "_accepted.json"))
        if saved != accepted or saved["profile"] != "formal":
            raise ValueError("Evaluation checkpoint is not the accepted formal S1")
        feedback(cfg, deployment, output, evaluation=True)


if __name__ == "__main__":
    main()
