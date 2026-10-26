"""Software fixtures for conservative controller continuation; no model calls."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from recoveragent.io import write_json


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    import continue_p3c

    return continue_p3c


def test_only_exact_unattempted_suffix_can_run(controller):
    plan = {"rows": [{"task_id": t} for t in ("a", "b", "c")]}
    assert controller.remaining_rows(plan, {"outcomes": plan["rows"][:2]}) == [
        {"task_id": "c"}
    ]
    for ids in ([], ["b"], ["a", "a"], ["a", "c"]):
        with pytest.raises(ValueError, match="exact prefix"):
            controller.remaining_rows(plan, {"outcomes": [{"task_id": t} for t in ids]})


@pytest.fixture
def stopped(tmp_path, monkeypatch, controller):
    result = {
        "fault": {"phase": "execution", "error_type": "TimeoutError"},
        "termination": "interrupted_batch",
        "usage": {"unknown_generation_reservation": 0},
        "natural_failure": False,
        "recovery_candidates": {},
        "boundaries": [{"snapshot_ref": "/remote/checkpoints/022_post"}],
    }
    event = {
        "status": "interrupted_batch",
        "error_type": "TimeoutError",
        "response_hash": "received",
        "history_hash": "history",
        "dispatched_count": 0,
        "tool_results": [],
    }
    interrupted = {
        "resume_eligible": False,
        "uncertain_call_id": None,
        "logical_state_hash": "db",
        "pre_batch_history_hash": "history",
    }
    write_json(tmp_path / "interaction/turns/023.json", {"event": event})
    write_json(tmp_path / "interaction/interruption/manifest.json", interrupted)
    monkeypatch.setattr(
        controller,
        "inspect_snapshot",
        lambda _: ({"logical_state_hash": "db", "history_hash": "history"}, {}, {}),
    )
    return tmp_path, result, event, interrupted


def test_budget_stop_is_audited_without_reclassifying_or_repairing_episode(
    controller, stopped
):
    folder, result, _, _ = stopped
    original = deepcopy(result)
    assert controller.budget_before_dispatch(folder, result)
    assert result == original
    assert result["natural_failure"] is False
    assert result["recovery_candidates"] == {}


@pytest.mark.parametrize(
    "change",
    [
        "unknown_usage",
        "attempted",
        "returned",
        "inflight",
        "changed_db",
        "changed_history",
    ],
)
def test_ambiguous_or_partial_dispatch_never_bypasses_stop(controller, stopped, change):
    folder, result, event, interrupted = stopped
    if change == "unknown_usage":
        result["usage"]["unknown_generation_reservation"] = 4096
    elif change == "attempted":
        event["appworld_attempted_count"] = 1
    elif change == "returned":
        event["dispatched_count"] = 1
    elif change == "inflight":
        event["inflight_call_id"] = "unknown"
    elif change == "changed_db":
        interrupted["logical_state_hash"] = "changed"
    else:
        interrupted["pre_batch_history_hash"] = "future"
    (folder / "interaction/turns/023.json").write_text(json.dumps({"event": event}))
    (folder / "interaction/interruption/manifest.json").write_text(
        json.dumps(interrupted)
    )
    assert not controller.budget_before_dispatch(folder, result)
