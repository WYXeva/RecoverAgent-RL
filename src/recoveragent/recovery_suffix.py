"""B10 Teacher takeover and suffix-only evidence/encoding; no training."""

import copy
from decimal import Decimal
import json
from pathlib import Path

from .answer_guard import AnswerGuard, PROTOCOL as ANSWER_PROTOCOL
from .execution_v6 import SYSTEM_PROMPT
from .full732_recovery_tasks import validate_context
from .harness import PROTOCOL as HARNESS_PROTOCOL, REFACTOR_REVISION
from .io import digest, files_manifest, read_json, write_json
from .schema_snapshot import prepare_snapshot_adapter
from .state import inspect_snapshot, restore

FORMAT = "full732-teacher-recovery-suffix-v1"
PROTOCOL = "b10_teacher_checkpoint_takeover_v1"
HANDOFF = {
    "role": "user",
    "content": "Continue the original task from the current state and visible history. Check what has already been done and avoid duplicating completed operations.",
}


def initial_guard(messages, recovery):
    guard = AnswerGuard(json.loads(messages[1]["content"])["instruction"])
    state = copy.deepcopy(recovery["initial_answer_guard"])
    if (
        set(state) != set(vars(guard))
        or state["contract"] != guard.contract
        or state["stopped"]
    ):
        raise ValueError("Invalid or exhausted source answer guard")
    if state["expected"] is not None:
        state["expected"] = Decimal(state["expected"])
    vars(guard).update(state)
    return guard


def restore_teacher_start(adapter, source, output, trajectory):
    manifest, context, _ = inspect_snapshot(source)
    agent = read_json(Path(source) / "agent_runtime.json")
    if (
        not manifest["metadata"].get("resume_eligible")
        or not manifest["metadata"].get("task_active")
        or agent["usage"].get("unknown_generation_reservation", 0)
    ):
        raise ValueError("Source is not a complete, active, resolved boundary")
    prepare_snapshot_adapter(adapter, manifest, context, Path(output) / "source_schema")
    restore(adapter, source)
    if adapter.world.task_completed():
        raise ValueError("Cannot recover a completed task")
    boundary = len(context["messages"])
    recovery = {
        "protocol": PROTOCOL,
        "snapshot_ref": str(Path(source).resolve()),
        "checkpoint_id": manifest["checkpoint_id"],
        "snapshot_prefix_end": boundary,
        "suffix_start": boundary + 1,
        "prefix_history_sha256": digest(context["messages"]),
        "visible_tools_sha256": digest(context["tools"]),
        "logical_state_hash": manifest["logical_state_hash"],
        "schema_binding": manifest["versions"]["schema_compatibility"],
        "initial_answer_guard": agent["answer_guard"],
        "student_prefix_usage": agent["usage"],
        "budget_policy": "independent Teacher request/token/time budget; preserve pending numeric correction; new bounded protocol-correction budget",
    }
    messages = copy.deepcopy(context["messages"]) + [copy.deepcopy(HANDOFF)]
    guard = initial_guard(messages, recovery)
    trajectory["recovery"] = recovery
    write_json(
        Path(output) / "restored_start.json",
        {
            **recovery,
            "source_unchanged": inspect_snapshot(source)[0] == manifest,
            "task_actions_executed": 0,
        },
    )
    return messages, copy.deepcopy(context["tools"]), guard


def validate_public_record(record):
    context = validate_context(record["task_id"], record["custom_research"])
    recovery = record["recovery"]
    boundary = recovery["snapshot_prefix_end"]
    if (
        record["split"] != context["official_split"]
        or record["scenario_id"] != record["task_id"].rsplit("_", 1)[0]
        or record["messages"][0] != {"role": "system", "content": SYSTEM_PROMPT}
        or record["start_kind"] != "checkpoint"
        or recovery["protocol"] != PROTOCOL
        or boundary < 2
        or recovery["suffix_start"] != boundary + 1
        or record["messages"][boundary] != HANDOFF
        or digest(record["messages"][:boundary]) != recovery["prefix_history_sha256"]
        or record.get("answer_guard", {}).get("protocol") != ANSWER_PROTOCOL
        or record.get("harness", {}).get("protocol") != HARNESS_PROTOCOL
        or record["harness"].get("runtime_revision") != REFACTOR_REVISION
        or record["tool_mode"] != "dynamic_discovery"
    ):
        raise ValueError("Invalid public recovery contract/prefix")


def process_attempt(directory, tokenizer):
    """Use existing receipt/filter/encoding machinery; reject entire overlong suffix."""
    from .demonstrations import filter_attempt, as_demo
    from .base_sft import assistant_examples
    from .b10_pilot import configuration

    directory = Path(directory)
    record = read_json(directory / "trajectory.json")
    config = configuration()
    result = {
        "status": "rejected",
        "eligible": False,
        "deferred": False,
        "examples": 0,
        "supervised_tokens": 0,
    }
    evaluation = (
        read_json(directory / "evaluation.json")
        if (directory / "evaluation.json").exists()
        else None
    )
    reasons = (
        ["infrastructure_error"]
        if record.get("status") != "valid"
        else filter_attempt(
            record,
            evaluation,
            set(config["tasks"]),
            {"repeated_identical_call_limit": 3},
            directory,
        )
    )
    if not reasons:
        source, context, _ = inspect_snapshot(record["recovery"]["snapshot_ref"])
        boundary = record["recovery"]["snapshot_prefix_end"]
        if (
            source["checkpoint_id"] != record["recovery"]["checkpoint_id"]
            or record["messages"][:boundary] != context["messages"]
        ):
            raise ValueError("Recovery source prefix changed")
        sample = as_demo(record)
        write_json(directory / "recovery_sample.json", sample)
        try:
            examples = assistant_examples(sample, tokenizer)
            if any(sum(e["loss_mask"]) > 4096 for e in examples):
                raise ValueError("Target too long; refusing silent truncation")
        except ValueError as exc:
            if "too long" not in str(exc):
                raise
            reasons = [str(exc)]
            result.update(status="deferred", deferred=True)
        else:
            if not examples:
                reasons = ["no_accepted_teacher_assistant_targets"]
            else:
                for e in examples:
                    if e["assistant_index"] < sample["recovery"]["suffix_start"] or any(
                        x != -100 for x in e["labels"][: e["target_start"]]
                    ):
                        raise ValueError("Prefix/control tokens entered supervision")
                write_json(directory / "encoded_suffix.json", examples)
                result.update(
                    status="eligible",
                    eligible=True,
                    examples=len(examples),
                    supervised_tokens=sum(sum(e["loss_mask"]) for e in examples),
                )
    result["reasons"] = reasons
    write_json(directory / "suffix_acceptance.json", result)
    write_json(
        directory / "evidence_manifest.json", {"files": files_manifest(directory)}
    )
    return result
