#!/usr/bin/env python3
"""Evaluate one accepted Base-SFT checkpoint on the existing frozen dev24."""

import argparse
import time
from pathlib import Path

from recoveragent.demonstrations import sha256
from recoveragent.io import read_json, write_json
from recoveragent.v2_tasks import ROOT, development_tasks
from scripts.train_v2_base_64k import CONFIG as TRAIN_CONFIG
from scripts.train_v2_base_64k import validate_stage, verify_source

CONFIG = ROOT / "configs/v2_base_dev24_20261113.json"


def verify_evaluation_source(cfg):
    training = read_json(TRAIN_CONFIG)
    if verify_source(training) != cfg["training_source_lock_sha256"]:
        raise ValueError("Training source lock differs from evaluation contract")
    lock = read_json(ROOT / cfg["source_lock"])
    required = {str(CONFIG.relative_to(ROOT)), "scripts/evaluate_v2_checkpoint.py"}
    if not required <= lock["files"].keys():
        raise ValueError("Incomplete evaluation source lock")
    for name, expected in lock["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Invalid evaluation lock path")
        if sha256(ROOT / name) != expected:
            raise ValueError("Evaluation source changed: " + name)
    if (
        cfg["task_ids"] != development_tasks(24)
        or cfg["student_limits"] != training["student_limits"]
        or cfg["model"] != training["models"][training["selected_model"]]
    ):
        raise ValueError("Evaluation tasks, model, or budgets changed")
    return sha256(ROOT / cfg["source_lock"])


def accept_checkpoint(checkpoint, backup, cfg):
    from recoveragent.v2_training import verify_checkpoint

    checkpoint = Path(checkpoint)
    receipt = verify_checkpoint(checkpoint, cfg["model"])
    parent = checkpoint.parent
    training = read_json(TRAIN_CONFIG)
    validate_stage(
        receipt, "formal", read_json(parent / "data/preparation.json"), training
    )
    if read_json(parent / "summary.json")[
        "status"
    ] != "base_sft_complete" or receipt != read_json(parent / "S0_base_accepted.json"):
        raise ValueError("Formal training has not completed acceptance")
    backup = read_json(backup)
    if backup.get("verified_locally") is not True or backup.get(
        "checkpoint_receipt_sha256"
    ) != sha256(checkpoint / "checkpoint_receipt.json"):
        raise ValueError("Verified off-machine checkpoint backup required first")
    return receipt


def summarize(tasks, results, *, reason):
    valid = [r for r in results if type(r.get("success")) is bool]
    successes = sum(r["success"] for r in valid)
    complete = len(valid) == len(tasks)
    return {
        "status": "complete" if complete else reason,
        "planned_tasks": len(tasks),
        "attempted_tasks": len(results),
        "officially_evaluated_tasks": len(valid),
        "successes": successes,
        "natural_failures": len(valid) - successes,
        "infrastructure_errors": len(results) - len(valid),
        "remaining_task_ids": tasks[len(results) :],
        "success_rate_completed": successes / len(valid) if valid else None,
        "success_rate_dev24": successes / len(tasks) if complete else None,
        "results": results,
    }


def run_tasks(cfg, output, dispatch, *, deadline, clock=time.monotonic):
    """Never shorten a task's budget to squeeze another attempt before shutdown."""
    output = Path(output)
    tasks, results = cfg["task_ids"], []
    reserve = cfg["student_limits"]["wall_seconds"] + cfg["evaluation_reserve_seconds"]
    reason = "insufficient_time_for_full_task_budget"
    for task in tasks:
        if deadline - clock() < reserve:
            break
        write_json(output / "started" / f"{len(results):02d}.json", {"task_id": task})
        try:
            result = dispatch(task, output / "tasks" / task)
            if result["task_id"] != task:
                raise ValueError("Evaluation returned the wrong task")
        except Exception as exc:
            result = {
                "task_id": task,
                "success": None,
                "classification": "infrastructure_error",
                "fault": {"type": type(exc).__name__, "message": str(exc)},
            }
        results.append(result)
        write_json(output / "progress" / f"{len(results):02d}.json", result)
        print(
            {
                "completed": len(results),
                "successes": sum(r.get("success") is True for r in results),
            },
            flush=True,
        )
        if result.get("classification") == "infrastructure_error":
            reason = "stopped_on_infrastructure_error"
            break
    summary = summarize(tasks, results, reason=reason)
    write_json(output / "summary.json", summary)
    return summary


def run(args):
    cfg = read_json(CONFIG)
    source_sha = verify_evaluation_source(cfg)
    receipt = accept_checkpoint(args.checkpoint, args.backup_receipt, cfg)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "run_config.json", cfg)
    write_json(output / "task_selection.json", {"task_ids": cfg["task_ids"]})
    write_json(
        output / "provenance.json",
        {
            "checkpoint": str(Path(args.checkpoint).resolve()),
            "checkpoint_receipt_sha256": sha256(
                Path(args.checkpoint) / "checkpoint_receipt.json"
            ),
            "adapter_sha256": receipt["adapter_sha256_after"],
            "source_lock_sha256": source_sha,
            "deadline_epoch": args.deadline_epoch,
            "backup_receipt": read_json(args.backup_receipt),
        },
    )
    deadline = time.monotonic() + max(0, args.deadline_epoch - time.time())
    if (
        deadline - time.monotonic()
        < cfg["student_limits"]["wall_seconds"] + cfg["evaluation_reserve_seconds"]
    ):
        return run_tasks(cfg, output, None, deadline=deadline)
    from peft import PeftModel, get_peft_model_state_dict
    from recoveragent.sft_recipe import tensor_hash
    from recoveragent.v2_inference import episode, policy_id
    from recoveragent.v2_model import load_base, load_tokenizer

    model = PeftModel.from_pretrained(
        load_base(cfg["model"]), args.checkpoint, is_trainable=False
    )
    if tensor_hash(get_peft_model_state_dict(model)) != receipt["adapter_sha256_after"]:
        raise ValueError("Loaded evaluation adapter differs from accepted checkpoint")
    tokenizer = load_tokenizer(cfg["model"])
    checkpoint_id = policy_id(model)

    def dispatch(task, directory):
        request = {
            "task_id": task,
            "model": cfg["model"],
            "checkpoint_id": checkpoint_id,
            "purpose": "evaluation",
            "output": str(directory),
            "appworld_root": args.appworld_root,
            "student_limits": cfg["student_limits"],
        }
        return episode(
            request, model, tokenizer, python=args.worker_python, deadline=deadline
        )

    return run_tasks(cfg, output, dispatch, deadline=deadline)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--backup-receipt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--appworld-root", required=True)
    parser.add_argument("--worker-python", required=True)
    parser.add_argument("--deadline-epoch", type=float, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
