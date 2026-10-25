"""Software-only fixtures. Real AppWorld process evidence is in probe_p3c.py."""

from pathlib import Path

import pytest

from recoveragent.io import digest, read_json, write_json
from recoveragent.p3c import classify_episode, drive, fixed_plan
from recoveragent.state import capture, inspect_snapshot, restore
from test_contracts import FakeAdapter, MESSAGES, TOOLS
from test_harness import session
from test_student_batch import block, receipt


def batch_messages():
    calls = [
        {
            "id": str(i),
            "type": "function",
            "function": {"name": "notes__write", "arguments": '{"value":2}'},
        }
        for i in range(2)
    ]
    return (
        MESSAGES
        + [{"role": "assistant", "content": "", "tool_calls": calls}]
        + [
            {"role": "tool", "tool_call_id": str(i), "content": "real fixture result"}
            for i in range(2)
        ]
    )


def test_v2_batch_state_roundtrip_and_immutable_agent_runtime(tmp_path):
    source = tmp_path / "cp"
    a = FakeAdapter()
    messages = batch_messages()
    runtime = {
        "policy": {"model": "fixture"},
        "usage": {"requests": 2},
        "harness": {"rejections": 1},
    }
    capture(
        a,
        source,
        messages,
        TOOLS,
        "post_action",
        {"task_active": True},
        agent_runtime=runtime,
    )
    original = inspect_snapshot(source)
    a.value = 9
    manifest, context = restore(a, source)
    assert (
        a.value == 1
        and context["messages"] == messages
        and manifest["format_version"] == 2
    )
    assert inspect_snapshot(source) == original
    (source / "agent_runtime.json").write_text("{}")
    with pytest.raises(ValueError, match="modified"):
        inspect_snapshot(source)


def test_batch_snapshot_requires_complete_history_and_v1_remains_single_call(tmp_path):
    a = FakeAdapter()
    messages = batch_messages()
    with pytest.raises(ValueError, match="one call"):
        capture(a, tmp_path / "old", messages, TOOLS, "pre_action", {})
    with pytest.raises(ValueError, match="unfinished batch"):
        capture(
            a,
            tmp_path / "partial",
            messages[:-1],
            TOOLS,
            "post_action",
            {},
            agent_runtime={},
        )
    assert not (tmp_path / "partial").exists()


@pytest.mark.parametrize(
    "evaluation,fault,expected",
    [
        ({"status": "valid", "success": False}, None, "model_failure"),
        ({"status": "valid", "success": True}, None, "success"),
        (
            {"status": "valid", "success": False},
            {"phase": "provider"},
            "infrastructure_error",
        ),
        (
            {"status": "valid", "success": False},
            {"phase": "partial_write"},
            "infrastructure_error",
        ),
        (
            {
                "status": "evaluation_error",
                "category": "environment_error",
                "success": None,
            },
            None,
            "evaluation_environment_error",
        ),
        (
            {"status": "evaluation_error", "category": "unknown", "success": None},
            None,
            "evaluation_unknown",
        ),
    ],
)
def test_only_trustworthy_policy_failure_can_enter_buffer(evaluation, fault, expected):
    result = classify_episode(evaluation, fault, "budget_exhausted")
    assert result["outcome"] == expected
    assert result["natural_failure"] == (expected == "model_failure")
    assert (
        result["reward"] is None
        if expected not in {"model_failure", "success"}
        else result["reward"] in (0, 1)
    )


def attach_state(adapter):
    adapter.task_id = "synthetic_1"
    adapter.versions = {"mock": True}
    adapter.value = 0
    adapter.fingerprint = lambda: digest(adapter.value)
    adapter.runtime = lambda: {"counter": len(adapter.calls)}
    adapter.export_state = lambda name, path: write_json(
        Path(path) / "state.json", adapter.value
    )


def test_driver_saves_errors_and_bounded_runtime_without_evaluation_in_history(
    tmp_path, monkeypatch
):
    s = session(monkeypatch)
    attach_state(s.adapter)
    r = drive(s, receipt("unexpected prose"), tmp_path, {"source": "software_fixture"})
    assert not r["fault"] and len(r["boundaries"]) == 4
    assert set(r["candidates"]) == {"late_failure"}
    for row in r["boundaries"]:
        manifest, context, _ = inspect_snapshot(row["snapshot_ref"])
        assert "evaluation" not in context and manifest["metadata"]["task_active"]
    pre = read_json(Path(r["boundaries"][0]["snapshot_ref"]) / "agent_runtime.json")
    post = read_json(Path(r["boundaries"][1]["snapshot_ref"]) / "agent_runtime.json")
    assert pre["harness"]["rejections"] == 0 and post["harness"]["rejections"] == 1
    assert pre["usage"]["model_requests"] == 0 and post["usage"]["model_requests"] == 1


def test_partial_write_is_audited_not_a_legal_recovery_boundary(tmp_path, monkeypatch):
    s = session(monkeypatch, output=tmp_path / "session")
    attach_state(s.adapter)

    def execute(name, args, tools):
        s.adapter.value += 1
        if s.adapter.value == 2:
            raise OSError("lost result after write")
        return '{"ok":true}', tools

    s.adapter.execute = execute
    r = drive(
        s, receipt(block("demo__login", {}) + block("demo__login", {})), tmp_path, {}
    )
    assert r["fault"] and s.adapter.value == 2 and len(r["boundaries"]) == 1
    interruption = read_json(tmp_path / "session/interruption/manifest.json")
    assert not interruption["resume_eligible"] and interruption[
        "logical_state_hash"
    ] == digest(2)
    assert s.events[-1]["dispatched_count"] == 1
    assert inspect_snapshot(r["boundaries"][0]["snapshot_ref"])[0][
        "logical_state_hash"
    ] == digest(0)
    assert not classify_episode(
        {"status": "valid", "success": False}, r["fault"], s.termination
    )["natural_failure"]


def test_generation_receipt_mismatch_is_infrastructure_not_a_natural_failure(
    tmp_path, monkeypatch
):
    s = session(monkeypatch)
    attach_state(s.adapter)
    r = drive(s, receipt(block("demo__login", {}), corrupt=True), tmp_path, {})
    assert r["fault"] and len(r["boundaries"]) == 1 and not s.adapter.calls
    assert not classify_episode(
        {"status": "valid", "success": False}, r["fault"], s.termination
    )["natural_failure"]


def test_budget_stop_preserves_last_complete_active_boundary(tmp_path, monkeypatch):
    s = session(monkeypatch, max_requests=1)
    attach_state(s.adapter)
    r = drive(s, receipt(block("demo__login", {})), tmp_path, {})
    assert not r["fault"] and s.termination == "budget_exhausted"
    late = r["candidates"]["late_failure"]
    _, context, _ = inspect_snapshot(late["snapshot_ref"])
    assert context["messages"][-1]["role"] == "tool"
    assert (
        read_json(Path(late["snapshot_ref"]) / "agent_runtime.json")["usage"][
            "model_requests"
        ]
        == 1
    )


def test_fixed_plan_unseen_priority_no_validation_leak_and_reproducible():
    pool = {f"{s}_{i}": s for s in ("a", "b", "c", "v") for i in range(5)}
    split = {s: "train" if s != "v" else "validation" for s in ("a", "b", "c", "v")}
    seen = ["a_0", "a_1", "a_2", "b_0", "b_1", "b_2", "c_0", "c_1", "c_2"]
    excluded = ["c_4"]
    settings = {"seed": 42, "num_tasks": 12, "strategy": "unseen_then_balanced_seen"}
    p = fixed_plan(pool, settings, seen, excluded, split)
    assert p == fixed_plan(
        dict(reversed(list(pool.items()))), settings, seen, excluded, split
    )
    assert len(p["tasks"]) == 12 and all(r["scenario_id"] != "v" for r in p["rows"])
    assert not set(p["tasks"]) & set(excluded)
    unseen = {t for t, s in pool.items() if s != "v" and t not in seen + excluded}
    assert unseen <= set(p["tasks"])
    assert all(
        (r["exposure"] == "base_sft_seen_task") == (r["task_id"] in seen)
        for r in p["rows"]
    )


def test_business_api_error_has_pre_and_post_candidates_without_service_fault(
    tmp_path, monkeypatch
):
    s = session(monkeypatch, max_requests=1)
    attach_state(s.adapter)

    def business_failure(name, args, tools):
        s.adapter.value = 3  # Some public errors can accompany actual partial changes.
        return "Execution failed. Response status code is 401", tools

    s.adapter.execute = business_failure
    r = drive(s, receipt(block("demo__login", {})), tmp_path, {})
    assert r["fault"] is None
    assert set(r["candidates"]) == {"pre_error", "post_error", "late_failure"}
    assert inspect_snapshot(r["candidates"]["pre_error"]["snapshot_ref"])[0][
        "logical_state_hash"
    ] == digest(0)
    assert inspect_snapshot(r["candidates"]["post_error"]["snapshot_ref"])[0][
        "logical_state_hash"
    ] == digest(3)


def test_controller_stops_at_worker_fault_and_preserves_earlier_natural_failure(
    tmp_path, monkeypatch
):
    from recoveragent import p3c

    config_path = tmp_path / "config.json"
    config = {
        "checkpoint": "fixture",
        "checkpoint_release": "fixture",
        "checkpoint_release_sha256": "fixture",
        "inference_url": "http://127.0.0.1:8766",
        "minimum_gpu_memory_mib": 90000,
        "student_limits": {"wall_seconds": 600, "max_generated_tokens": 16384},
    }
    write_json(config_path, config)
    rows = [{"task_id": f"t{i}", "scenario_id": "s"} for i in range(3)]
    monkeypatch.setattr(p3c, "verify_source", lambda: "software_lock")
    monkeypatch.setattr(
        p3c, "validate_config", lambda c: {"rows": rows, "plan_id": "software_plan"}
    )
    monkeypatch.setattr(p3c, "verify_checkpoint", lambda *a: {"fixture": True})
    monkeypatch.setattr(
        p3c,
        "resource_preflight",
        lambda *a: {"gpus": [{"total_mib": 98000}], "free_disk_bytes": 30 * 1024**3},
    )
    monkeypatch.setattr(p3c, "request_json", lambda *a, **k: {"fixture": True})
    calls = []

    def worker(command, **kwargs):
        task = command[command.index("--task") + 1]
        calls.append(task)
        directory = Path(command[command.index("--output") + 1])
        result = {
            "task_id": task,
            "scenario_id": "s",
            "policy": {"fixture": True},
            "failure_type": "budget_exhausted",
            "natural_failure": task == "t0",
            "outcome": "model_failure" if task == "t0" else "infrastructure_error",
            "usage": {
                "model_requests": 1,
                "unknown_generation_reservation": 4096 if task == "t1" else 0,
            },
            "recovery_candidates": {},
            "boundaries": [],
        }
        write_json(directory / "result.json", result)

    monkeypatch.setattr(p3c.subprocess, "run", worker)
    summary = p3c.collect(config_path, tmp_path / "run")
    assert calls == ["t0", "t1"] and summary["status"] == "BLOCKED"
    assert (
        summary["natural_failures"] == 1
        and summary["usage"]["unknown_generation_reservation"] == 4096
    )
    failures = read_json(tmp_path / "run/failures.json")
    assert len(failures) == 1 and failures[0]["task_id"] == "t0"


def test_controller_insufficient_gpu_never_contacts_model(tmp_path, monkeypatch):
    from recoveragent import p3c

    config_path = tmp_path / "config.json"
    write_json(
        config_path,
        {
            "checkpoint": "fixture",
            "checkpoint_release": "fixture",
            "checkpoint_release_sha256": "fixture",
            "minimum_gpu_memory_mib": 90000,
        },
    )
    monkeypatch.setattr(p3c, "verify_source", lambda: "lock")
    monkeypatch.setattr(p3c, "validate_config", lambda c: {"rows": []})
    monkeypatch.setattr(p3c, "verify_checkpoint", lambda *a: {"fixture": True})
    monkeypatch.setattr(
        p3c,
        "resource_preflight",
        lambda *a: {"gpus": [{"total_mib": 8151}], "free_disk_bytes": 30 * 1024**3},
    )

    def forbidden(*a, **kw):
        raise AssertionError("Must not request a model")

    monkeypatch.setattr(p3c, "request_json", forbidden)
    result = p3c.collect(config_path, tmp_path / "run")
    assert (
        result["attempted_tasks"] == 0
        and result["stop_reason"] == "blocked:RuntimeError"
    )
