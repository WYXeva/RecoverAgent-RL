"""Fixed three-source short update from S0 and same-protocol paired dev24."""

import argparse
from pathlib import Path
import time

from .demonstrations import sha256
from .io import read_json, write_json
from .teacher_answer_format import PROTOCOL
from .teacher_escalation import train_from_s0
from .v2_data import write_rows
from .v2_runtime import S1_SHORT_RUNTIME as RUNTIME, S1_RETRAIN_RUNTIME, verify_lock
from .v2_short_data import materialize
from .v2_tasks import ROOT, plan


def configuration():
    verify_lock(RUNTIME)
    cfg = read_json(ROOT / f"configs/{RUNTIME}.json")
    previous = read_json(ROOT / f"configs/{S1_RETRAIN_RUNTIME}.json")
    if (
        any(
            cfg[key] != previous[key]
            for key in (
                "dev_task_ids",
                "student_limits",
                "models",
                "s0",
                "lora",
                "environment_contract",
            )
        )
        or cfg["dev_task_ids"] != plan()["dev24"]
        or cfg["answer_format_contract"] != PROTOCOL
        or cfg["teacher"]["answer_format_contract"] != PROTOCOL
        or cfg["execution_protocol"] != RUNTIME
        or cfg["recovery_sft"]["max_steps"] != 40
        or cfg["presentations"]
        != {"old_replay": 160, "fresh_full": 96, "recovery_suffix": 64}
        or cfg["teacher_collection_enabled"]
    ):
        raise ValueError("Short update or paired evaluation contract changed")
    return cfg


def prepare(output, deployment, cfg):
    from .v2_training import validate_training_rows

    frozen = ROOT / cfg["training_plan"]
    if sha256(frozen) != cfg["training_plan_sha256"]:
        raise ValueError("Frozen training plan changed")
    selection = read_json(frozen)
    rows = materialize(Path(deployment["data_sources"]), cfg, selection)
    validate_training_rows(rows, cfg["student_limits"]["max_input_tokens"])
    stage = cfg["recovery_sft"]
    if (
        len(rows)
        != stage["max_steps"]
        * stage["per_device_train_batch_size"]
        * stage["gradient_accumulation_steps"]
    ):
        raise ValueError("Presentations do not equal formal optimizer schedule")
    target = output / "training_data"
    target.mkdir(parents=True, exist_ok=False)
    write_rows(target / "examples.jsonl", rows)
    write_json(target / "selection.json", selection)
    write_json(
        target / "manifest.json",
        {
            "revision": RUNTIME,
            "expected_parent": cfg["s0"],
            "source_lock_sha256": verify_lock(RUNTIME)["lock_sha256"],
            "examples_sha256": sha256(target / "examples.jsonl"),
            "training_plan_sha256": cfg["training_plan_sha256"],
            "examples": len(rows),
            "groups": selection["groups"],
            "max_sequence_tokens": max(len(r["input_ids"]) for r in rows),
            "truncated": False,
            "historical_text_changed": False,
            "validation_data_used": False,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=["prepare", "train", "train-worker", "baseline", "evaluation"]
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
            raise ValueError("Short training worker configuration changed")
        payload["deadline"] = time.monotonic() + max(
            0, payload.pop("deadline_epoch") - time.time()
        )
        train_sft(cfg, **payload)
        return
    from .v2_s1_batch import initialize, feedback, checkpoint

    output, deployment = args.output.resolve(), read_json(args.deployment)
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
            worker_script=ROOT / "scripts/train_v2_s1_short.py",
        )
    else:
        if args.stage == "evaluation":
            saved = checkpoint(deployment["s1"], cfg)
            if (
                saved != read_json(output / (cfg["output_name"] + "_accepted.json"))
                or saved["optimizer_steps"] != 40
                or saved["profile"] != "formal"
            ):
                raise ValueError("Evaluation requires the accepted 40-step S1")
        feedback(
            cfg,
            deployment,
            output,
            evaluation=True,
            evaluation_model="s0" if args.stage == "baseline" else "s1",
        )


if __name__ == "__main__":
    main()
