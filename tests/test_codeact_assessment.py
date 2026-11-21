"""Frozen comparison wiring and synthetic CPU scheduling; no model calls."""

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from recoveragent.codeact import assessment as a, rubric
from recoveragent.io import read_json, write_json
from recoveragent.v2_tasks import CODEACT_TEST, context_for, validate_context

ROOT = Path(__file__).resolve().parents[1]


def settings():
    return read_json(ROOT / "configs/codeact_test111_20261122.json")


def test_frozen_test_scope_is_evaluation_only():
    cfg = settings()
    assert len(cfg["evaluation"]["tasks"]) == 111
    assert len({t.split("_")[0] for t in cfg["evaluation"]["tasks"]}) == 37
    for task in cfg["evaluation"]["tasks"]:
        ctx = context_for(task, final_test=CODEACT_TEST)
        assert validate_context(task, ctx) == ctx
        with pytest.raises(ValueError):
            context_for(task)
        with pytest.raises(ValueError):
            context_for(task, training=True, final_test=CODEACT_TEST)
    with pytest.raises(ValueError, match="Unknown final test"):
        context_for(cfg["evaluation"]["tasks"][0], final_test="unknown")


def test_api_audit_preserves_calls_and_counts_caught_errors(tmp_path):
    response = SimpleNamespace(status_code=200)
    calls = []

    def request(app, api, **kwargs):
        calls.append((app, api, kwargs))
        if api == "bad":
            raise ValueError("Response status code is 422: bad parameter")
        return response

    requester = SimpleNamespace(_request=request)
    audit = rubric.ApiAudit(requester, tmp_path)
    audit.request_index = 3
    assert requester._request(_app_name="gmail", _api_name="show", x=2) is response
    with pytest.raises(ValueError, match="422"):
        requester._request("gmail", "bad")
    requester._request("admin", "show")
    requester._request("gmail", "show", track=False)
    assert not (tmp_path / "api_outcomes.jsonl").exists()
    assert len(audit.pending) == 2
    audit.flush()
    assert audit.pending == []
    assert calls[0] == (
        "gmail",
        "show",
        {"client": None, "raise_on_failure": None, "track": True, "x": 2},
    )
    write_json(
        tmp_path / "trajectory.json",
        {
            "actions": [
                {
                    "execution_error": True,
                    "observation": "Execution failed.\nKeyError: x",
                },
                {"execution_error": False, "observation": "caught API error in Python"},
            ],
            "format_rejections": 1,
        },
    )
    result = {
        "valid": True,
        "success": False,
        "termination": "task_completed",
        "evaluation": {
            "official": {"passes": [{}], "failures": [{"requirement": "goal"}]}
        },
    }
    stats = rubric.diagnostics(tmp_path, result)
    assert (
        stats["api_attempts"],
        stats["api_errors"],
        stats["code_execution_errors"],
    ) == (2, 1, 1)
    assert stats["api_error_types"] == {"http_422": 1}
    assert stats["official_checks_failed"] == 1
    assert stats["submitted_but_failed"] is True
    result["valid"] = False
    assert rubric.diagnostics(tmp_path, result)["official_checks_failed"] is None


def test_rolling_refills_without_batch_barrier():
    first = threading.Event()
    fifth = threading.Event()

    def job(task):
        if task == 0:
            first.set()
            assert fifth.wait(3), "Task 5 should start before slow task 1 finishes"
        elif task == 4:
            fifth.set()
        return task

    completed = []
    assert (
        a.rolling(
            list(range(6)),
            4,
            lambda pool, t: pool.submit(job, t),
            lambda t, r: completed.append(r),
            executor_factory=lambda: ThreadPoolExecutor(4),
        )
        == []
    )
    assert first.is_set() and fifth.is_set() and sorted(completed) == list(range(6))


def test_capacity_fallback_resume_and_next_task_four(tmp_path, monkeypatch):
    cfg = settings()
    cfg["evaluation"]["tasks"] = [
        "oom",
        "normal_fail",
        "scoring_error",
        "good",
        "later",
    ]
    model = cfg["evaluation"]["models"][0]
    calls, widths = [], []

    class Server:
        def __init__(self, config, deployment, actor, path, *, concurrency):
            widths.append(concurrency)
            self.proof = {"max_num_seqs": concurrency}
            self.process = SimpleNamespace(poll=lambda: None)
            self.receipt = path / "receipt.json"

        def close(self):
            pass

    def task(config, deployment, m, req, directory):
        t, attempt = req["task_id"], req["attempt"]
        calls.append((t, attempt, req["server"]["max_num_seqs"]))
        if t != "oom":
            time.sleep(0.05)  # OOM completes before refilling the fifth task.
        valid = t not in ("oom", "scoring_error")
        row = {
            "binding": a.attempt_binding(config, m, t),
            "directory": directory,
            "task_id": t,
            "success": (t != "normal_fail") if valid else None,
            "reward_valid": valid,
            "failure_stage": "evaluation" if t == "scoring_error" else "sampling",
            "fault": "CUDA out of memory" if t == "oom" else None,
            "usage": {"requests": 1},
            "rubric": {},
            "initial_state_hash": "clean",
        }
        write_json(Path(directory) / "evaluation_row.json", row)
        return row

    original_rolling = a.rolling

    def rolling(tasks, width, submit, complete):
        return original_rolling(
            tasks,
            width,
            submit,
            complete,
            executor_factory=lambda: ThreadPoolExecutor(width),
        )

    monkeypatch.setattr(a, "Server", Server)
    monkeypatch.setattr(a, "evaluate_task", task)
    monkeypatch.setattr(a, "rolling", rolling)
    result = a.evaluate_model(cfg, {"models": {"S0": "actor"}}, model, tmp_path)
    assert widths == [4, 2, 4]
    assert result["tasks"] == 5 and result["attempts"] == 6
    assert result["failure"] == 1 and result["unknown"] == 2
    assert ("oom", 2, 2) in calls and ("later", 1, 4) in calls
    assert sum(t == "normal_fail" for t, _, _ in calls) == 1
    assert sum(t == "scoring_error" for t, _, _ in calls) == 1
    before = copy.deepcopy(calls)
    assert a.evaluate_model(cfg, {"models": {"S0": "actor"}}, model, tmp_path) == result
    assert calls == before
    cfg["seed"] += 1
    with pytest.raises(ValueError, match="identity"):
        a.recover_attempt(cfg, model, "oom", tmp_path / "tasks/oom/attempt_1", "cached")


def test_finished_row_checks_same_test_protocol(tmp_path):
    cfg = settings()
    task, model = cfg["evaluation"]["tasks"][0], cfg["evaluation"]["models"][0]
    write_json(
        tmp_path / "episode/result.json",
        {
            "success": False,
            "valid": True,
            "termination": "task_completed",
            "usage": {"requests": 2},
            "seconds": 1,
            "trajectory_sha256": "synthetic",
            "evaluation": {"official": {"passes": [], "failures": []}},
        },
    )
    trajectory = {
        "task_id": task,
        "config_sha256": a.digest(cfg),
        "custom_research": context_for(task, final_test=CODEACT_TEST),
        "student_execution": a.execution_identity(cfg),
        "initial_state_hash": "clean",
        "actions": [],
    }
    write_json(tmp_path / "episode/trajectory.json", trajectory)
    row = a.finish_attempt(cfg, model, task, tmp_path)
    assert row["success"] is False and row["reward_valid"] is True
    trajectory["custom_research"].pop("final_test_protocol")
    a.replace_json(tmp_path / "episode/trajectory.json", trajectory)
    with pytest.raises(ValueError, match="provenance"):
        a.finish_attempt(cfg, model, task, tmp_path)


def test_suite_source_binding_and_same_protocol_resume(tmp_path, monkeypatch):
    cfg = settings()
    deployment = {"models": {m["name"]: m["name"] for m in cfg["evaluation"]["models"]}}
    monkeypatch.setattr(
        a,
        "verify_checkpoint",
        lambda actor, _: next(
            m for m in cfg["evaluation"]["models"] if m["name"] == actor
        ),
    )
    monkeypatch.setattr(a, "verify_source", lambda _: "source-A")
    monkeypatch.setattr(a, "evaluate_model", lambda *args: {"tasks": 111})
    assert len(a.suite(cfg, deployment, tmp_path)) == 3
    assert len(a.suite(cfg, deployment, tmp_path)) == 3
    monkeypatch.setattr(a, "verify_source", lambda _: "source-B")
    with pytest.raises(ValueError, match="binding changed"):
        a.suite(cfg, deployment, tmp_path)
