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


@pytest.fixture
def feedback_prefix(clock_handoff, tmp_path):
    from recoveragent.v2_feedback_resume import carry_feedback

    original_source, cfg, identity, s0, deployment = clock_handoff
    first, _, _ = carry_clock_interruption(*clock_handoff)
    source = tmp_path / "feedback_source"
    old = read_json(ROOT / "configs/v2_round1_clock_20261109.json")
    deployment = {**deployment, "feedback_source_directory": str(ROOT)}
    write_json(source / "run_config.json", old)
    write_json(
        source / "preflight.json",
        {
            "source": {"lock_sha256": sha256(ROOT / old["source_lock"])},
            "deployment": {"job_deadline_epoch": deployment["job_deadline_epoch"]},
        },
    )
    write_json(
        source / "continued_base.json",
        read_json(original_source / "continued_base.json"),
    )
    write_json(
        source / "task_lists.json", read_json(original_source / "task_lists.json")
    )
    write_json(source / "carried_clock_interruption.json", first)
    task = plan()["feedback20"][1]
    directory = source / "feedback" / f"feedback_01_{task}"
    result = {
        "task_id": task,
        "classification": "infrastructure_error",
        "fault": None,
        "evaluation": {
            "status": "evaluation_error",
            "category": "unknown",
            "official_result_complete": True,
        },
        "success": None,
        "policy": first["policy"],
        "usage": {"generated_tokens": 2, "unknown_generation_reservation": 0},
    }
    write_json(directory / "result.json", result)
    write_json(
        directory.with_name(directory.name + ".config.json"),
        {
            "task_id": task,
            "model": identity,
            "checkpoint_id": first["policy"]["checkpoint_id"],
            "purpose": "feedback",
            "student_limits": cfg["student_limits"],
            "output": str(directory),
        },
    )
    write_json(
        directory / "session/requests/0.request.json", {"policy": first["policy"]}
    )
    write_json(
        directory / "session/requests/0.response.json",
        {"policy": first["policy"], "token_ids": [1, 2]},
    )
    write_json(
        source / "round_summary.json",
        {
            "status": "stopped",
            "feedback": [first, {**result, "directory": str(directory)}],
            "training_generated_tokens": 6,
        },
    )
    return carry_feedback, (source, cfg, identity, s0, deployment)


def test_unknown_reward_is_preserved_and_completed_prefix_not_replayed(feedback_prefix):
    call, args = feedback_prefix
    rows, tokens, proof = call(*args)
    assert [x["task_id"] for x in rows] == plan()["feedback20"][:2]
    assert tokens == 6 and proof["completed_tasks"] == 2
    assert rows[1]["classification"] == "infrastructure_error"
    assert rows[1]["success"] is None
    assert all(x["retry_performed"] is False for x in rows)
    assert proof["no_replay"] and proof["unknown_rewards_preserved"]
    assert (
        rows[0]["teacher_source_lock_sha256"] != rows[1]["teacher_source_lock_sha256"]
    )


@pytest.mark.parametrize("damage", ["unfinished", "lost_cost", "deadline", "result"])
def test_feedback_prefix_tampering_rejected(feedback_prefix, damage):
    call, args = feedback_prefix
    source, cfg, identity, s0, deployment = args
    path = source / "round_summary.json"
    value = read_json(path)
    if damage == "unfinished":
        (source / "feedback" / f"feedback_02_{plan()['feedback20'][2]}").mkdir()
    elif damage == "lost_cost":
        value["training_generated_tokens"] -= 1
    elif damage == "deadline":
        deployment["job_deadline_epoch"] += 1
    else:
        value["feedback"][1]["success"] = False
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        call(source, cfg, identity, s0, deployment)


@pytest.mark.parametrize(
    "change",
    [
        {"fault": {"type": "OOM"}},
        {"success": False},
        {
            "evaluation": {
                "status": "evaluation_error",
                "category": "environment_error",
                "official_result_complete": True,
            }
        },
        {
            "evaluation": {
                "status": "evaluation_error",
                "category": "unknown",
                "official_result_complete": False,
            }
        },
        {"usage": {"unknown_generation_reservation": 12}},
    ],
)
def test_only_complete_unknown_evaluation_can_continue(change):
    from recoveragent.v2_feedback_resume import resumable_evaluation_error

    result = {
        "classification": "infrastructure_error",
        "fault": None,
        "success": None,
        "evaluation": {
            "status": "evaluation_error",
            "category": "unknown",
            "official_result_complete": True,
        },
        "usage": {"unknown_generation_reservation": 0},
    }
    assert resumable_evaluation_error(result)
    assert not resumable_evaluation_error({**result, **change})


def test_feedback15_keeps_every_other_budget():
    from recoveragent.v2_resume import feedback_recipe

    cfg = config()
    old = read_json(ROOT / "configs/v2_round1_feedback_20261109.json")
    assert cfg["feedback_tasks"] == 15
    assert {
        k: v
        for k, v in feedback_recipe(cfg, old).items()
        if k not in {"format", "source_lock"}
    } == {k: v for k, v in old.items() if k not in {"format", "source_lock"}}
    assert cfg["dev_tasks"] == 24 and cfg["grpo"]["groups"] == 16


def test_feedback_prefix_chain_preserves_original_provenance(feedback_prefix, tmp_path):
    call, args = feedback_prefix
    source, cfg, identity, s0, deployment = args
    rows, used, proof = call(*args)
    next_source = tmp_path / "next_source"
    old = read_json(ROOT / "configs/v2_round1_feedback_20261109.json")
    write_json(next_source / "run_config.json", old)
    write_json(
        next_source / "preflight.json",
        {
            "source": {"lock_sha256": sha256(ROOT / old["source_lock"])},
            "deployment": {"job_deadline_epoch": deployment["job_deadline_epoch"]},
        },
    )
    for name in ["task_lists.json", "continued_base.json"]:
        write_json(next_source / name, read_json(source / name))
    write_json(next_source / "continued_feedback.json", proof)
    write_json(
        next_source / "round_summary.json",
        {"status": "stopped", "feedback": rows, "training_generated_tokens": used},
    )
    write_json(next_source / "feedback/handoff_marker.json", {"new_requests": 0})
    again, counted, _ = call(next_source, cfg, identity, s0, deployment)
    assert again == rows and counted == used


def test_user_canceled_sixteenth_task_cost_is_retained_but_not_training(
    feedback_prefix,
):
    import copy

    call, args = feedback_prefix
    source, cfg, identity, s0, deployment = args
    summary = read_json(source / "round_summary.json")
    template = copy.deepcopy(summary["feedback"][1])
    original_dir = Path(template["directory"])
    original_request = read_json(
        original_dir.with_name(original_dir.name + ".config.json")
    )
    for i in range(2, 15):
        task = plan()["feedback20"][i]
        directory = source / "feedback" / f"feedback_{i:02d}_{task}"
        row = {**template, "task_id": task, "directory": str(directory)}
        write_json(
            directory / "result.json",
            {k: v for k, v in row.items() if k != "directory"},
        )
        write_json(
            directory.with_name(directory.name + ".config.json"),
            {**original_request, "task_id": task, "output": str(directory)},
        )
        for name in ["0.request.json", "0.response.json"]:
            write_json(
                directory / "session/requests" / name,
                read_json(original_dir / "session/requests" / name),
            )
        summary["feedback"].append(row)
        summary["training_generated_tokens"] += 2
    partial = source / "feedback" / f"feedback_15_{plan()['feedback20'][15]}"
    policy = template["policy"]
    for i in range(2):
        write_json(
            partial / f"session/requests/{i}.request.json",
            {"policy": policy, "max_new_tokens": 4},
        )
    write_json(
        partial / "session/requests/0.response.json",
        {"policy": policy, "token_ids": [1, 2]},
    )
    write_json(partial / "backend_failure.json", {"error_type": "TimeoutError"})
    summary["training_generated_tokens"] += 2
    (source / "round_summary.json").write_text(json.dumps(summary))
    write_json(
        source / "feedback_limit_interruption.json",
        {
            "reason": "user_reduce_feedback15",
            "feedback_limit": 15,
            "no_retry": True,
            "source_summary_sha256": sha256(source / "round_summary.json"),
            "partial_files": files_manifest(partial),
        },
    )
    rows, used, proof = call(*args)
    assert len(rows) == 15 and used == summary["training_generated_tokens"]
    assert proof["generation_reservation"] == 4
    assert proof["cancellation"]["known_generated_tokens"] == 2
    assert proof["cancellation"]["training_eligible"] is False
    assert plan()["feedback20"][15] not in [r["task_id"] for r in rows]
