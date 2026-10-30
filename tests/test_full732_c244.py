"""Frozen membership, unknown scores and independent engine binding; no model calls."""

import copy

import pytest

from recoveragent import full732_c244 as c, student, student_evaluation
from recoveragent.io import read_json


def config():
    protocol = read_json(c.PROTOCOL)
    _, tasks = c.c_tasks()
    return dict(
        protocol["execution"],
        scope=c.SCOPE,
        expected_policy=protocol["model_identity"] | {"checkpoint_id": "adapter:test"},
        m1_parameter_hash="test",
        inference_url="http://127.0.0.1:8768",
        cases=[
            {
                "task_id": r["task_id"],
                "scenario_id": r["scenario_id"],
                "partition": "C244",
                "split": r["official_split"],
            }
            for r in tasks
        ],
    )


def test_C_is_exactly_244_and_disjoint_from_training_recovery():
    plan, rows = c.c_tasks()
    ids = {r["task_id"] for r in rows}
    assert len(ids) == 244
    assert not ids & {
        r["task_id"] for r in plan["rows"] if r["role"] != "shared_validation"
    }


@pytest.mark.parametrize(
    "change", ["omit", "reorder", "replace", "memory", "budget", "raw"]
)
def test_config_refuses_task_or_protocol_drift(monkeypatch, change):
    monkeypatch.setattr(c, "verify_lock", lambda: {})
    cfg = copy.deepcopy(config())
    if change == "omit":
        cfg["cases"].pop()
    elif change == "reorder":
        cfg["cases"].reverse()
    elif change == "replace":
        cfg["cases"][0]["task_id"] = "not_a_C_task"
    elif change == "memory":
        cfg["memory"]["enabled"] = True
    elif change == "budget":
        cfg["student_limits"]["max_requests"] += 1
    else:
        cfg["expected_policy"]["checkpoint_id"] = "raw_base:test"
    with pytest.raises(ValueError):
        c.validate_config(cfg)


def test_unknown_and_unattempted_are_not_failures_or_complete_TGC():
    cfg = config()
    rows = [
        {"status": "valid", "official_success": True},
        {"status": "valid", "official_success": False},
        {"status": "evaluation_error", "official_success": None},
        {"status": "infrastructure_error", "official_success": None},
    ]
    result = c.summarize(cfg, rows)
    assert (result["success"], result["failure"], result["unknown"]) == (1, 1, 2)
    assert result["TGC"] is None and len(result["unattempted"]) == 240
    complete = c.summarize(cfg, [rows[0]] * 100 + [rows[1]] * 144)
    assert complete["TGC"] == 100 / 244


def test_reuses_engine_without_mutating_historical_modules():
    runner = c.engine()
    assert (
        runner.run_episode.__code__.co_code
        == student_evaluation.run_episode.__code__.co_code
    )
    assert (
        runner.StudentSession.step.__code__.co_code
        == student.StudentSession.step.__code__.co_code
    )
    assert runner.verify_execution_runtime is c.verify_lock
    assert (
        runner.StudentSession.__init__.__globals__["verify_student_runtime"]
        is c.verify_lock
    )
    assert student.verify_student_runtime is not c.verify_lock
    assert student_evaluation.EvaluationAdapter is not c.CAdapter


def test_non_C_rejected_before_opening_task_or_world(tmp_path):
    plan, _ = c.c_tasks()
    row = next(r for r in plan["rows"] if r["role"] == "base_sft")
    with pytest.raises(ValueError, match="Only the frozen C244"):
        c.task_provenance(tmp_path, row["task_id"], row["official_split"])


def test_release_requires_formal_training_and_reload(tmp_path, monkeypatch):
    from recoveragent.io import write_json

    monkeypatch.setattr(c, "verify_training_lock", lambda: "lock")
    write_json(
        tmp_path / "recoveragent_checkpoint.json",
        {"status": "PASS", "profile": "short_smoke"},
    )
    with pytest.raises(ValueError, match="formal checkpoint"):
        c.verified_checkpoint(tmp_path)
