"""Synthetic queue workers only; no Teacher or AppWorld calls."""

from pathlib import Path

import pytest

from recoveragent.codeact import collect as module
from recoveragent.codeact import data
from recoveragent.io import digest, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup_queue(monkeypatch):
    config = read_json(ROOT / "configs/codeact_reboot_20261119.json")
    rows = module.queue()[:4]
    monkeypatch.setattr(module, "queue", lambda: rows)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "audit_first_batch", lambda *args: {"passed": True})
    monkeypatch.setattr(data, "export", lambda *args: {"synthetic_export": True})
    config["collection"].update(max_tasks=4, first_batch_tasks=1)
    return config, rows


def result_at(target, task, attempt, success, *, fault=None, category="unknown"):
    trajectory = {"fault": fault}
    write_json(target / "trajectory.json", trajectory)
    result = {
        "task_id": task,
        "attempt": attempt,
        "valid": success is not None,
        "success": success,
        "eligible": success is True,
        "trajectory_sha256": digest(trajectory),
        "evaluation": {
            "status": "valid" if success is not None else "evaluation_error",
            "category": category,
            "success": success,
        },
    }
    write_json(target / "result.json", result)
    return result


def test_resume_skips_scoring_task_preserves_original_and_stops_at_target(
    tmp_path, setup_queue, monkeypatch
):
    config, rows = setup_queue
    config_hash = digest(config)
    # One old success, one evaluator exception at attempt 1. Do not retry this task.
    success = tmp_path / "episodes" / rows[0]["task_id"] / "attempt_1"
    result_at(success, rows[0]["task_id"], 1, True)
    unknown = tmp_path / "episodes" / rows[1]["task_id"] / "attempt_1"
    result_at(unknown, rows[1]["task_id"], 1, None)
    originals = {p: p.read_bytes() for p in unknown.glob("*.json")}
    calls = []

    def worker(command, **kwargs):
        task = command[command.index("--task") + 1]
        attempt = int(command[command.index("--attempt") + 1])
        target = Path(command[command.index("--output") + 1])
        calls.append((task, attempt))
        result_at(target, task, attempt, True)

    monkeypatch.setattr(module.subprocess, "run", worker)
    module.collect(
        config,
        tmp_path,
        "/unused",
        "/unused",
        target_successes=2,
        skip_scoring_errors=True,
    )
    # Omitted controls on resume inherit the persistent stop condition.
    module.collect(config, tmp_path, "/unused", "/unused")
    assert calls == [(rows[2]["task_id"], 1)]
    assert not (unknown.parent / "attempt_2").exists()
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert digest(read_json(tmp_path / "config.json")) == config_hash
    status = read_json(tmp_path / "progress.json")
    assert status["status"] == "target_reached"
    assert status["eligible_tasks"] == 2
    assert status["skipped_scoring_tasks"] == 1
    assert status["invalid_attempts"] == 1
    assert status["finished_tasks"] == 3
    assert status["export"]["synthetic_export"]
    with pytest.raises(ValueError, match="controls differ"):
        module.collect(config, tmp_path, "unused", "unused", target_successes=3)


@pytest.mark.parametrize(
    "fault,category,skip",
    [
        (None, "unknown", True),
        (None, None, True),
        ("Teacher timeout", "unknown", False),
        (None, "environment_error", False),
    ],
)
def test_live_scoring_skip_does_not_swallow_service_or_environment_faults(
    tmp_path, setup_queue, monkeypatch, fault, category, skip
):
    config, rows = setup_queue
    calls = []

    def worker(command, **kwargs):
        task = command[command.index("--task") + 1]
        attempt = int(command[command.index("--attempt") + 1])
        target = Path(command[command.index("--output") + 1])
        calls.append((task, attempt))
        result_at(
            target,
            task,
            attempt,
            None if task == rows[0]["task_id"] else True,
            fault=fault,
            category=category,
        )

    monkeypatch.setattr(module.subprocess, "run", worker)
    module.collect(
        config,
        tmp_path,
        "unused",
        "unused",
        target_successes=1,
        skip_scoring_errors=True,
    )
    status = read_json(tmp_path / "progress.json")
    assert status["status"] == ("target_reached" if skip else "blocked")
    assert len(calls) == (2 if skip else 1)
    assert not (tmp_path / "episodes" / rows[0]["task_id"] / "attempt_2").exists()


def test_normal_failures_retry_once_and_report_exhausted_queue(
    tmp_path, setup_queue, monkeypatch
):
    config, rows = setup_queue
    calls = []

    def worker(command, **kwargs):
        task = command[command.index("--task") + 1]
        attempt = int(command[command.index("--attempt") + 1])
        target = Path(command[command.index("--output") + 1])
        calls.append((task, attempt))
        result_at(target, task, attempt, False)

    monkeypatch.setattr(module.subprocess, "run", worker)
    module.collect(
        config,
        tmp_path,
        "unused",
        "unused",
        target_successes=2,
        skip_scoring_errors=True,
    )
    status = read_json(tmp_path / "progress.json")
    assert status["status"] == "queue_exhausted"
    assert len(calls) == len(rows) * 2
    assert status["valid_failed_attempts"] == 8
    assert status["skipped_scoring_tasks"] == 0


def test_scoring_skip_requires_intact_trajectory(tmp_path):
    result = result_at(tmp_path, "synthetic", 1, None)
    assert module.official_scoring_error(tmp_path, result)
    result["trajectory_sha256"] = "changed"
    assert not module.official_scoring_error(tmp_path, result)


def test_reached_target_export_failure_never_collects_more(
    tmp_path, setup_queue, monkeypatch
):
    config, rows = setup_queue
    result_at(
        tmp_path / "episodes" / rows[0]["task_id"] / "attempt_1",
        rows[0]["task_id"],
        1,
        True,
    )

    def bad_export(*args):
        raise ValueError("encoding problem")

    monkeypatch.setattr(data, "export", bad_export)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **kw: pytest.fail("No calls")
    )
    for _ in range(2):
        with pytest.raises(ValueError, match="encoding problem"):
            module.collect(config, tmp_path, "unused", "unused", target_successes=1)
        assert (
            read_json(tmp_path / "progress.json")["status"]
            == "target_reached_export_error"
        )
