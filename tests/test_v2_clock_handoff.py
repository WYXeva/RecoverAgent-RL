"""Synthetic immutable feedback handoff; no model or real task calls."""

import json
from pathlib import Path

import pytest

from recoveragent.demonstrations import sha256
from recoveragent.io import digest, files_manifest, read_json, write_json
from recoveragent.v2_resume import carry_clock_interruption
from recoveragent.v2_round import config
from recoveragent.v2_tasks import ROOT, plan


@pytest.fixture
def clock_handoff(tmp_path):
    cfg = config()
    identity = cfg["models"][cfg["selected_model"]]
    old = read_json(ROOT / "configs/v2_round1_resume12h_20261109.json")
    source, s0 = tmp_path / "source", tmp_path / "S0"
    write_json(s0 / "checkpoint_receipt.json", {"adapter_sha256_after": "adapter"})
    policy = {**identity, "checkpoint_id": digest({"adapter_sha256": "adapter"})}
    task = plan()["feedback20"][0]
    directory = source / "feedback" / f"feedback_00_{task}"
    result = {
        "task_id": task,
        "classification": "natural_failure",
        "evaluation": {"status": "valid", "success": False},
        "wall_seconds": 38,
        "policy": policy,
        "usage": {"generated_tokens": 4, "unknown_generation_reservation": 0},
    }
    write_json(directory / "result.json", result)
    write_json(
        directory.with_name(directory.name + ".config.json"),
        {
            "task_id": task,
            "purpose": "feedback",
            "model": identity,
            "checkpoint_id": policy["checkpoint_id"],
            "student_limits": cfg["student_limits"],
            "output": str(directory),
        },
    )
    for i in range(2):
        write_json(
            directory / f"session/requests/{i}.request.json",
            {"policy": policy, "remaining_wall_seconds": 1200},
        )
        write_json(
            directory / f"session/requests/{i}.response.json",
            {"policy": policy, "token_ids": [1, 2]},
        )
    write_json(source / "run_config.json", old)
    write_json(
        source / "preflight.json",
        {
            "source": {"lock_sha256": sha256(ROOT / old["source_lock"])},
            "deployment": {"job_deadline_epoch": 9000},
        },
    )
    write_json(
        source / "round_summary.json",
        {
            "status": "stopped",
            "feedback": [{**result, "directory": str(directory)}],
            "training_generated_tokens": 4,
        },
    )
    write_json(
        source / "continued_base.json",
        {
            "checkpoint_receipt_sha256": {
                "S0_base": sha256(s0 / "checkpoint_receipt.json")
            }
        },
    )
    write_json(
        source / "task_lists.json",
        {
            "feedback20": plan()["feedback20"],
            "dev24": plan()["dev24"],
            "split_manifest_sha256": plan()["manifest_sha256"],
        },
    )
    write_json(
        source / "controller_interruption.json",
        {
            "reason_code": "appworld_frozen_monotonic",
            "retry_completed_task": False,
            "source_summary_sha256": sha256(source / "round_summary.json"),
            "feedback_files": files_manifest(source / "feedback"),
            "completed_task_controller_wall_seconds": 38,
            "completed_result_epoch": 1000,
            "pause_epoch": 1010,
            "teacher_source_directory": str(ROOT),
        },
    )
    return source, cfg, identity, s0, {"job_deadline_epoch": 9000}


def test_completed_feedback_and_token_cost_are_carried_once(clock_handoff):
    result, tokens, reserved = carry_clock_interruption(*clock_handoff)
    assert result["task_id"] == plan()["feedback20"][0]
    assert result["classification"] == "natural_failure"
    assert result["clock_metadata_invalid"] and result["actual_wall_budget_verified"]
    assert not result["retry_performed"]
    assert tokens == 4 and reserved == 0
    assert result["teacher_source_directory"] == str(ROOT)


@pytest.mark.parametrize(
    "damage",
    [
        "deadline",
        "request_bytes",
        "result_bytes",
        "second_request",
        "teacher_started",
        "over_budget",
        "source_lock",
    ],
)
def test_unsafe_clock_handoff_fails_closed(clock_handoff, damage):
    source, cfg, identity, s0, deployment = clock_handoff
    control = source / "controller_interruption.json"
    evidence = read_json(control)
    directory = source / "feedback" / f"feedback_00_{plan()['feedback20'][0]}"
    if damage == "deadline":
        deployment["job_deadline_epoch"] += 1
    elif damage == "request_bytes":
        (directory / "session/requests/0.request.json").write_text("{}")
    elif damage == "result_bytes":
        (directory / "result.json").write_text("{}")
    elif damage == "second_request":
        write_json(
            source
            / "feedback"
            / f"feedback_01_{plan()['feedback20'][1]}"
            / "session/requests/0.request.json",
            {},
        )
    elif damage == "teacher_started":
        (source / "recovery").mkdir()
    elif damage == "over_budget":
        evidence["completed_task_controller_wall_seconds"] = 1201
    elif damage == "source_lock":
        evidence["teacher_source_directory"] = str(source)
    control.write_text(json.dumps(evidence))
    with pytest.raises((ValueError, FileNotFoundError)):
        carry_clock_interruption(source, cfg, identity, s0, deployment)


def test_legacy_dispatch_uses_original_source_and_lock(monkeypatch):
    from recoveragent import v2_legacy_teacher as legacy

    calls = []

    def check_output(args, **kwargs):
        calls.append((args, kwargs))
        return '{"status":"eligible"}'

    monkeypatch.setattr(legacy.subprocess, "check_output", check_output)
    assert legacy.call(
        "python", "/old/source", "original-lock", "suffix", {"directory": "/run"}
    ) == {"status": "eligible"}
    args, kwargs = calls[0]
    payload = json.loads(args[-1])
    assert payload["expected_lock"] == "original-lock"
    assert "verify_lock()" in args[2]
    assert kwargs["cwd"] == "/old/source"
    assert kwargs["env"]["PYTHONPATH"] == str(Path("/old/source/src"))
    with pytest.raises(ValueError):
        legacy.call("python", "/old/source", "lock", "unsafe", {})
