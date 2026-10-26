"""Continue only the unattempted suffix of a frozen P3-C plan.

Retains every original episode classification. A proven wall-budget stop before
any batch dispatch is separately audited and excluded from recovery training;
it need not prevent fresh, unrelated tasks from running. No request is retried.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

from recoveragent.demonstrations import sha256
from recoveragent.io import files_manifest, read_json, write_json
from recoveragent.p3c import (
    resource_preflight,
    validate_config,
    verify_checkpoint,
    verify_source,
)
from recoveragent.state import inspect_snapshot
from recoveragent.student_evaluation import request_json

REVISION = "p3c_unattempted_continuation_20261026"


def save_progress(output, summary):
    temporary = Path(output) / ".summary.tmp"
    temporary.write_text(json.dumps(summary, indent=2) + "\n")
    temporary.replace(Path(output) / "summary.json")


def verify_episode(directory):
    directory = Path(directory)
    actual = files_manifest(directory)
    actual.pop("manifest.json")
    if actual != read_json(directory / "manifest.json")["files"]:
        raise ValueError("Previous episode evidence changed")
    return read_json(directory / "result.json")


def budget_before_dispatch(directory, result):
    """Conservative evidence check, valid only with the sealed Student source."""
    fault = result.get("fault") or {}
    if (
        fault.get("phase") != "execution"
        or fault.get("error_type") != "TimeoutError"
        or result.get("termination") != "interrupted_batch"
        or result.get("usage", {}).get("unknown_generation_reservation", 0)
        or result.get("natural_failure")
        or result.get("recovery_candidates")
        or not result.get("boundaries")
    ):
        return False
    directory = Path(directory)
    turns = sorted((directory / "interaction/turns").glob("*.json"))
    if not turns:
        return False
    event = read_json(turns[-1])["event"]
    if (
        event.get("status") != "interrupted_batch"
        or event.get("error_type") != "TimeoutError"
        or event.get("appworld_attempted_count", 0)
        or event.get("dispatched_count", 0)
        or event.get("tool_results")
        or event.get("inflight_call_id")
        or not event.get("response_hash")
    ):
        return False
    interrupted = read_json(directory / "interaction/interruption/manifest.json")
    boundary = result["boundaries"][-1]
    snapshot = directory / "checkpoints" / Path(boundary["snapshot_ref"]).name
    previous, _, _ = inspect_snapshot(snapshot)
    return (
        interrupted["resume_eligible"] is False
        and interrupted["uncertain_call_id"] is None
        and interrupted["logical_state_hash"] == previous["logical_state_hash"]
        and interrupted["pre_batch_history_hash"] == previous["history_hash"]
        and event["history_hash"] == previous["history_hash"]
    )


def remaining_rows(plan, previous):
    attempted = [row["task_id"] for row in previous["outcomes"]]
    planned = [row["task_id"] for row in plan["rows"]]
    if not attempted or attempted != planned[: len(attempted)]:
        raise ValueError("Previous tasks must be a nonempty exact prefix of the plan")
    return plan["rows"][len(attempted) :]


def run(command, log, timeout):
    with Path(log).open("x") as stream:
        subprocess.run(
            command,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=timeout,
        )


def collect(config_path, previous, output):
    previous, output = Path(previous), Path(output)
    config, prior = read_json(config_path), read_json(previous / "summary.json")
    base_lock = verify_source()
    root = Path(__file__).resolve().parents[1]
    lock_path = root / "configs/p3c_continuation_20261026_lock.json"
    lock = read_json(lock_path)
    if lock["revision"] != REVISION:
        raise ValueError("Unexpected continuation protocol")
    for name, expected in lock["files"].items():
        if sha256(root / name) != expected:
            raise ValueError("Continuation source changed: " + name)
    plan = validate_config(config)
    pending = remaining_rows(plan, prior)
    preflight = read_json(previous / "preflight.json")
    identity = verify_checkpoint(
        config["checkpoint"],
        config["checkpoint_release"],
        config["checkpoint_release_sha256"],
    )
    if (
        preflight["config_sha256"] != sha256(config_path)
        or preflight["source_lock_sha256"] != base_lock
        or prior["policy"] != identity
        or request_json(config["inference_url"], timeout=15) != identity
    ):
        raise ValueError("Continuation must use identical config, source, and model")
    for row in prior["outcomes"]:
        result = verify_episode(previous / row["task_id"])
        if result["outcome"] not in {
            "success",
            "model_failure",
        } and not budget_before_dispatch(previous / row["task_id"], result):
            raise ValueError("Unresolved prior infrastructure or evaluation failure")
    output.mkdir(parents=True, exist_ok=False)
    resources = resource_preflight(output)
    if resources["free_disk_bytes"] < 10 * 1024**3 or not any(
        gpu["total_mib"] >= config["minimum_gpu_memory_mib"]
        for gpu in resources["gpus"]
    ):
        raise RuntimeError("Remote resources unavailable")
    summary = {
        "format": REVISION,
        "status": "RUNNING",
        "previous_summary_sha256": sha256(previous / "summary.json"),
        "config_sha256": sha256(config_path),
        "source_lock_sha256": base_lock,
        "continuation_lock_sha256": sha256(lock_path),
        "plan_id": plan["plan_id"],
        "policy": identity,
        "planned_tasks": [r["task_id"] for r in pending],
        "outcomes": [],
        "independent_restorations": [],
        "stop_reason": None,
    }
    try:
        for row in pending:
            folder = output / row["task_id"]
            summary["outcomes"].append(
                {
                    "task_id": row["task_id"],
                    "outcome": "infrastructure_error",
                    "controller_category": "worker_outcome_pending",
                    "usage": {
                        "unknown_generation_reservation": config["student_limits"][
                            "max_generated_tokens"
                        ]
                    },
                }
            )
            save_progress(output, summary)
            run(
                [
                    sys.executable,
                    "-m",
                    "recoveragent.p3c",
                    "episode",
                    "--config",
                    str(config_path),
                    "--output",
                    str(folder),
                    "--task",
                    row["task_id"],
                ],
                output / (row["task_id"] + ".log"),
                config["student_limits"]["wall_seconds"] + 300,
            )
            result = verify_episode(folder)
            category = result["outcome"]
            if budget_before_dispatch(folder, result):
                category = "budget_interruption_before_dispatch"
            summary["outcomes"][-1].update(
                {
                    "task_id": row["task_id"],
                    "outcome": result["outcome"],
                    "controller_category": category,
                    "usage": result["usage"],
                }
            )
            if category not in {
                "success",
                "model_failure",
                "budget_interruption_before_dispatch",
            }:
                raise RuntimeError(
                    "Infrastructure or evaluation fault; no automatic retry"
                )
            candidates = {
                c["checkpoint_id"]: c for c in result["recovery_candidates"].values()
            }
            for index, candidate in enumerate(candidates.values()):
                target = output / ("restore_" + row["task_id"] + f"_{index}.json")
                run(
                    [
                        sys.executable,
                        "-m",
                        "recoveragent.p3c",
                        "restore",
                        "--config",
                        str(config_path),
                        "--source",
                        candidate["snapshot_ref"],
                        "--output",
                        str(target),
                        "--experiment",
                        output.name + "_restore_" + row["task_id"] + f"_{index}",
                    ],
                    target.with_suffix(".log"),
                    180,
                )
                restored = read_json(target)
                if (
                    restored["status"] != "PASS"
                    or restored["checkpoint_id"] != candidate["checkpoint_id"]
                ):
                    raise ValueError("Restoration failed")
                summary["independent_restorations"].append(restored)
            save_progress(output, summary)
        summary["status"] = "PASS"
    except Exception as exc:
        summary.update(
            status="BLOCKED", stop_reason=type(exc).__name__ + ": " + str(exc)
        )
    finally:
        usage = Counter()
        for row in summary["outcomes"]:
            usage.update(row["usage"])
        results = [
            read_json(output / row["task_id"] / "result.json")
            for row in summary["outcomes"]
            if (output / row["task_id"] / "result.json").exists()
        ]
        write_json(
            output / "failures.json",
            [
                {
                    k: result[k]
                    for k in (
                        "task_id",
                        "scenario_id",
                        "policy",
                        "failure_type",
                        "recovery_candidates",
                    )
                }
                for result in results
                if result["natural_failure"]
            ],
        )
        summary["usage"] = dict(usage)
        summary["attempted_tasks"] = len(summary["outcomes"])
        summary["natural_failures"] = sum(r["natural_failure"] for r in results)
        summary["successes"] = sum(r["outcome"] == "success" for r in results)
        summary["checkpoints"] = sum(len(r["boundaries"]) for r in results)
        summary["unattempted"] = summary["planned_tasks"][len(summary["outcomes"]) :]
        save_progress(output, summary)
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--previous", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    collect(a.config, a.previous, a.output)
