"""Freeze B10 jobs and select immutable recovery boundaries; never call a model.

Actual Student jobs use the existing P3-C CLI. Teacher continuation and suffix
acceptance are separate execution gates, not simulated by this preparation tool.
"""

import argparse
import copy
import json
from pathlib import Path
import sys

from recoveragent.demonstrations import sha256
from recoveragent.full732_recovery_tasks import b_tasks
from recoveragent.io import digest, files_manifest, read_json, write_json
from recoveragent.p3c import observable_error, validate_config, verify_source
from recoveragent.schema_compatibility import SCHEMA_REVISION
from recoveragent.schema_snapshot import snapshot_revision
from recoveragent.state import inspect_snapshot

ROOT = Path(__file__).resolve().parents[1]
CONFIG = "configs/b10_recovery_pilot_20261104.json"
LOCK = "configs/b10_recovery_preparation_20261104_lock.json"


def checked_plan():
    lock = read_json(ROOT / LOCK)
    required = {
        CONFIG,
        "configs/b10_recovery_plan_20261104.json",
        "scripts/prepare_b10_recovery_pilot.py",
        "configs/p3c_nullable_v2_20261104.json",
        "configs/p3c_nullable_v2_20261104_lock.json",
        "configs/full732_official_retry_current_20261029.json",
    }
    if (
        lock.get("format") != "b10-recovery-preparation-lock-v1"
        or not required <= lock.get("files", {}).keys()
    ):
        raise ValueError("Incomplete B10 preparation lock")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("B10 preparation source changed: " + name)
    config = read_json(ROOT / CONFIG)
    if config["source_lock"] != LOCK:
        raise ValueError("Unexpected B10 source lock")
    for path_key, hash_key in (
        ("plan", "plan_sha256"),
        ("student_config_source", "student_config_source_sha256"),
        ("teacher_config_source", "teacher_config_source_sha256"),
    ):
        if sha256(ROOT / config[path_key]) != config[hash_key]:
            raise ValueError("B10 source reference changed")
    plan = read_json(ROOT / config["plan"])
    if digest({k: v for k, v in plan.items() if k != "plan_id"}) != plan["plan_id"]:
        raise ValueError("B10 plan identity changed")
    if plan["rows"] != b_tasks()[1][:10]:
        raise ValueError("B10 must be the existing B244 first ten in order")
    if config["student_config"] != read_json(ROOT / config["student_config_source"]):
        raise ValueError("B10 cannot change the approved Student protocol")
    verify_source(config["student_config"]["runtime_revision"])
    validate_config(config["student_config"])
    return config, plan


def prepare(output, *, appworld_root=None, checkpoint=None, inference_url=None):
    config, plan = checked_plan()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    student = copy.deepcopy(config["student_config"])
    for key, value in {
        "appworld_root": appworld_root,
        "checkpoint": checkpoint,
        "inference_url": inference_url,
    }.items():
        if value is not None:
            student[key] = value
    validate_config(student)
    write_json(output / "student_config.json", student)
    write_json(output / "tasks.json", plan)
    jobs = [
        {
            "task_id": row["task_id"],
            "official_split": row["official_split"],
            "attempt_limit": 1,
            "command": [
                sys.executable,
                "-m",
                "recoveragent.p3c",
                "episode",
                "--config",
                str(output / "student_config.json"),
                "--task",
                row["task_id"],
                "--output",
                str(output / "student" / row["task_id"]),
            ],
        }
        for row in plan["rows"]
    ]
    write_json(output / "student_jobs.json", jobs)
    write_json(
        output / "preparation.json",
        {
            "status": "PREPARED_NOT_EXECUTED",
            "plan_id": plan["plan_id"],
            "source_lock_sha256": sha256(ROOT / LOCK),
            "planned_student_tasks": 10,
            "max_teacher_attempts": 20,
            "actual_student_tasks": 0,
            "actual_teacher_attempts": 0,
            "teacher_continuation_and_suffix_acceptance": "NOT_IMPLEMENTED_BY_THIS_PREPARATION_TOOL",
            "files": files_manifest(output),
        },
    )
    return jobs


def select_checkpoints(episode):
    """Controller-only selection. Never forward the terminal result to Teacher."""
    config, plan = checked_plan()
    episode = Path(episode).resolve()
    sealed = read_json(episode / "manifest.json")
    actual = files_manifest(episode)
    actual.pop("manifest.json")
    if actual != sealed["files"]:
        raise ValueError("Student episode evidence changed")
    result = read_json(episode / "result.json")
    if result["task_id"] not in {r["task_id"] for r in plan["rows"]}:
        raise ValueError("Cannot use a task outside frozen B10")
    release = read_json(ROOT / config["student_config"]["checkpoint_release"])
    expected_policy = {
        k: release[k] for k in ("model", "revision", "thinking", "chat_template_sha256")
    }
    expected_policy["checkpoint_id"] = "adapter:" + release["adapter_tensor_sha256"]
    if result["policy"] != expected_policy:
        raise ValueError("Natural failure did not come from M1")
    if not (
        result["outcome"] == "model_failure"
        and result["natural_failure"] is True
        and result["evaluation"]["status"] == "valid"
        and result["evaluation"]["success"] is False
        and result.get("fault") is None
    ):
        return {"selected": [], "skipped": [{"reason": "not_valid_natural_failure"}]}
    selected, skipped, seen = [], [], set()
    for kind in plan["checkpoint_rules"]["order"]:
        candidate = result["recovery_candidates"].get(kind)
        if candidate is None:
            skipped.append({"kind": kind, "reason": "no_eligible_recorded_boundary"})
            continue
        source = Path(candidate["snapshot_ref"]).resolve()
        if not source.is_relative_to(episode / "checkpoints"):
            raise ValueError("Checkpoint is outside its Student episode")
        manifest, context, runtime = inspect_snapshot(source)
        agent = read_json(source / "agent_runtime.json")
        if (
            manifest["task_id"] != result["task_id"]
            or manifest["checkpoint_id"] != candidate["checkpoint_id"]
            or agent["policy"] != result["policy"]
            or snapshot_revision(manifest) != SCHEMA_REVISION
        ):
            raise ValueError("Recovery checkpoint identity/Schema differs")
        reason = None
        last_assistant = max(
            (i for i, m in enumerate(context["messages"]) if m["role"] == "assistant"),
            default=-1,
        )
        last_results = [
            m for m in context["messages"][last_assistant + 1 :] if m["role"] == "tool"
        ]
        if kind == "post_error" and (
            candidate.get("error_type") != "public_tool_error"
            or manifest["position"] != "post_action"
            or observable_error({"tool_results": last_results}) != "public_tool_error"
        ):
            reason = "not_post_observable_tool_error"
        elif not manifest["metadata"].get("task_active") or not manifest[
            "metadata"
        ].get("resume_eligible"):
            reason = "inactive_or_not_resumable"
        elif agent.get("usage", {}).get("unknown_generation_reservation", 0):
            reason = "unresolved_generation"
        elif (agent.get("answer_guard") or {}).get("stopped"):
            reason = "answer_guard_exhausted"
        identity = digest(
            [
                manifest[k]
                for k in ("history_hash", "tool_schema_hash", "logical_state_hash")
            ]
            + [digest(runtime)]
        )
        if identity in seen:
            reason = "duplicate_boundary"
        if reason:
            skipped.append({"kind": kind, "reason": reason})
            continue
        seen.add(identity)
        selected.append(
            {
                "kind": kind,
                "snapshot_ref": str(source),
                "checkpoint_id": manifest["checkpoint_id"],
                "prefix_end_index": len(context["messages"]),
                "schema_binding": manifest["versions"]["schema_compatibility"],
                "student_prefix_usage": agent["usage"],
                "teacher_attempt_limit": 1,
            }
        )
    return {"selected": selected, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "select"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--episode")
    parser.add_argument("--appworld-root")
    parser.add_argument("--checkpoint")
    parser.add_argument("--inference-url")
    args = parser.parse_args()
    if args.command == "prepare":
        jobs = prepare(
            args.output,
            appworld_root=args.appworld_root,
            checkpoint=args.checkpoint,
            inference_url=args.inference_url,
        )
        print(json.dumps({"status": "PREPARED_NOT_EXECUTED", "jobs": len(jobs)}))
    else:
        if not args.episode:
            parser.error("select requires --episode")
        result = select_checkpoints(args.episode)
        write_json(args.output, result)
        print(
            json.dumps(
                {"selected": len(result["selected"]), "skipped": len(result["skipped"])}
            )
        )


if __name__ == "__main__":
    main()
