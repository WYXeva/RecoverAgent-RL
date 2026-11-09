#!/usr/bin/env python3
"""Qualify an immutable generated pool; no Student, GPU or task re-execution."""

import argparse
from pathlib import Path
import time

from recoveragent.coevolve_generation import Evolution, TaskQualityError
from recoveragent.coevolve_tasks import validate_task
from recoveragent.demonstrations import sha256
from recoveragent.io import digest, read_json, write_json
from recoveragent.v2_finish_resume import carry_s1
from recoveragent.v2_round import config
from recoveragent.v2_runtime import verify_lock


def review(payload):
    cfg = config()
    locked = verify_lock()
    source, code = Path(payload["source_run"]), Path(payload["source_code"])
    deployment = read_json(payload["deployment"])
    preflight = read_json(source / "preflight.json")
    original = read_json(source / "run_config.json")
    old_lock = code / original["source_lock"]
    if (
        preflight["source"]["lock_sha256"] != sha256(old_lock)
        or preflight["deployment"]["job_deadline_epoch"]
        != deployment["job_deadline_epoch"]
        or any(
            sha256(code / name) != value
            for name, value in read_json(old_lock)["files"].items()
        )
    ):
        raise ValueError("Saved-pool source/deadline mismatch")
    identity = cfg["models"][cfg["selected_model"]]
    carried, _, _, proof = carry_s1(
        payload["s1_run"], cfg, identity, payload["s0_checkpoint"], deployment
    )
    if read_json(source / "continued_s1.json") != proof:
        raise ValueError("Saved-pool feedback provenance changed")
    number = cfg["task_evolution"]["max_feedback_cycles"]
    pool_path = source / "coevolve" / f"pool_after_cycle_{number:02d}.json"
    snapshot = read_json(pool_path)
    prior_cost = snapshot["teacher_usage_priced_cny_including_prior_run"]
    if prior_cost < carried["teacher_usage_priced_cny"]:
        raise ValueError("Saved-pool cost ledger decreased")
    remaining = deployment["job_deadline_epoch"] - time.time()
    if remaining <= 0:
        raise TimeoutError("The authorized run deadline has expired")
    output = Path(payload["output"])
    output.mkdir(parents=True, exist_ok=False)
    summary = {
        "status": "started",
        "source": locked,
        "source_pool_sha256": sha256(pool_path),
        "source_pool": str(pool_path),
        "prior_teacher_usage_priced_cny": prior_cost,
        "teacher_usage_priced_cny": prior_cost,
        "feedback": carried["feedback"],
        "results": [],
        "student_requests": 0,
        "new_explorations_or_solvers": 0,
        "original_tasks_and_rewards_unchanged": True,
    }
    evolution = Evolution(
        cfg, identity, deployment, output, summary, time.monotonic() + remaining, None
    )
    write_json(output / "input.json", payload)
    try:
        for task in snapshot["pool"]:
            validate_task(task, task["source_task_id"])
            directory = output / task["task_id"]
            write_json(directory / "original_task.json", task)
            result = {"task_id": task["task_id"], "source_sha256": digest(task)}
            try:
                result["quality"] = evolution.quality(task, directory)
                result["status"] = "qualified"
                evolution.pool.append(task)
            except (ValueError, KeyError, TypeError) as exc:
                result.update(
                    status=exc.quality_status
                    if isinstance(exc, TaskQualityError)
                    else "rejected_quality",
                    reason=str(exc),
                )
            summary["results"].append(result)
            write_json(directory / "qualification.json", result)
        if sha256(pool_path) != summary["source_pool_sha256"]:
            raise ValueError("Source pool changed during read-only review")
        write_json(output / "qualified_pool.json", evolution.pool)
        summary["status"] = "complete"
    except Exception as exc:
        summary.update(status="stopped", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        summary["additional_teacher_usage_priced_cny"] = (
            summary["teacher_usage_priced_cny"] - prior_cost
        )
        summary.pop("feedback")
        write_json(output / "review_summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    result = review(read_json(args.input))
    print({"status": result["status"], "results": len(result["results"])})


if __name__ == "__main__":
    main()
