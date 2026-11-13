"""Single-checkpoint evaluation, synthetic only; no model or AppWorld calls."""

import copy
from pathlib import Path

import pytest

from scripts import evaluate_v2_checkpoint as entry
from recoveragent.io import read_json


def configuration():
    return read_json(entry.CONFIG)


def test_evaluation_source_and_frozen_dev24():
    path = Path("configs/v2_base_dev24_envfix_20261113.json")
    cfg = read_json(path)
    assert len(entry.verify_evaluation_source(cfg, path)) == 64
    assert cfg["task_ids"] == configuration()["task_ids"]
    assert cfg["student_limits"] == configuration()["student_limits"]
    with pytest.raises(ValueError, match="source lock|source changed"):
        entry.verify_evaluation_source(configuration())


@pytest.mark.parametrize("field", ["task_ids", "student_limits", "model"])
def test_evaluation_contract_drift_is_rejected(field):
    cfg = copy.deepcopy(configuration())
    if field == "task_ids":
        cfg[field] = list(reversed(cfg[field]))
    elif field == "student_limits":
        cfg[field]["wall_seconds"] = 60
    else:
        cfg[field]["revision"] = "different"
    with pytest.raises(ValueError, match="tasks, model, or budgets"):
        entry.verify_evaluation_source(cfg)


def test_no_dispatch_when_full_task_budget_does_not_fit(tmp_path):
    cfg = configuration()

    def forbidden(*args):
        pytest.fail("Task started without its full budget")

    summary = entry.run_tasks(cfg, tmp_path, forbidden, deadline=1319, clock=lambda: 0)
    assert summary["attempted_tasks"] == 0
    assert summary["success_rate_dev24"] is None
    assert summary["remaining_task_ids"] == cfg["task_ids"]


def test_partial_results_preserve_order_and_do_not_claim_full_success_rate(tmp_path):
    cfg = configuration()
    called, now = [], [0]

    def dispatch(task, directory):
        called.append(task)
        now[0] += 1000
        return {"task_id": task, "classification": "success", "success": True}

    summary = entry.run_tasks(
        cfg, tmp_path, dispatch, deadline=2500, clock=lambda: now[0]
    )
    assert called == cfg["task_ids"][:2]
    assert summary["successes"] == 2
    assert summary["success_rate_completed"] == 1
    assert summary["success_rate_dev24"] is None
    assert summary["remaining_task_ids"] == cfg["task_ids"][2:]


def test_infrastructure_error_is_saved_and_not_retried(tmp_path):
    cfg = configuration()
    called = []

    def dispatch(task, directory):
        called.append(task)
        raise RuntimeError("synthetic backend failure")

    summary = entry.run_tasks(cfg, tmp_path, dispatch, deadline=100000, clock=lambda: 0)
    assert called == cfg["task_ids"][:1]
    assert summary["infrastructure_errors"] == 1
    assert summary["natural_failures"] == 0
    assert summary["status"] == "stopped_on_infrastructure_error"
    assert read_json(tmp_path / "progress/01.json")["success"] is None


def test_full_dev24_rate_uses_official_successes(tmp_path):
    cfg = configuration()

    def dispatch(task, directory):
        ok = cfg["task_ids"].index(task) % 2 == 0
        return {
            "task_id": task,
            "success": ok,
            "classification": "success" if ok else "natural_failure",
        }

    summary = entry.run_tasks(cfg, tmp_path, dispatch, deadline=100000, clock=lambda: 0)
    assert summary["status"] == "complete"
    assert summary["successes"] == summary["natural_failures"] == 12
    assert summary["success_rate_dev24"] == 0.5


def test_checkpoint_backup_required_before_loading(tmp_path, monkeypatch):
    from recoveragent import v2_training
    from recoveragent.io import write_json

    checkpoint = tmp_path / "S0_base"
    receipt = {"synthetic": True}
    write_json(checkpoint / "checkpoint_receipt.json", receipt)
    write_json(tmp_path / "data/preparation.json", {})
    write_json(tmp_path / "summary.json", {"status": "base_sft_complete"})
    write_json(tmp_path / "S0_base_accepted.json", receipt)
    monkeypatch.setattr(v2_training, "verify_checkpoint", lambda *args: receipt)
    monkeypatch.setattr(entry, "validate_stage", lambda *args: None)
    backup = tmp_path / "backup.json"
    write_json(backup, {"verified_locally": True, "checkpoint_receipt_sha256": "wrong"})
    with pytest.raises(ValueError, match="off-machine checkpoint backup"):
        entry.accept_checkpoint(checkpoint, backup, configuration())


def test_environment_probe_required_and_all_apps_checked(tmp_path):
    from recoveragent.appworld_environment import REVISION, VERSIONS
    from recoveragent.io import write_json

    cfg = {"environment_contract": REVISION}
    with pytest.raises(ValueError, match="before model load"):
        entry.accept_environment_probe(None, cfg)
    evidence = {
        "passed": True,
        "model_requests": 0,
        "environment": {
            "revision": REVISION,
            "versions": VERSIONS,
            "snapshot_roundtrip": True,
        },
        "cases": [
            {"app": app, "passed": True}
            for app in [
                "amazon",
                "file_system",
                "gmail",
                "phone",
                "simple_note",
                "splitwise",
                "spotify",
                "todoist",
                "venmo",
            ]
        ],
    }
    path = tmp_path / "good.json"
    write_json(path, evidence)
    assert len(entry.accept_environment_probe(path, cfg)["sha256"]) == 64
    evidence["cases"][0]["passed"] = False
    path = tmp_path / "bad.json"
    write_json(path, evidence)
    with pytest.raises(ValueError, match="incomplete or incompatible"):
        entry.accept_environment_probe(path, cfg)
