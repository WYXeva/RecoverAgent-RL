"""Synthetic scheduling/continuation checks, no model or paid Teacher calls."""

import json

import pytest

from recoveragent.io import read_json, write_json
from recoveragent.s2_schedule import GroupFeedback, TaskQueue, select_contexts
from recoveragent.v2_tasks import plan


def cfg():
    config = read_json("configs/codeact_grpo_coevolve_parallel_20261120.json")
    return {
        "feedback_task_ids": config["online"]["train_tasks"],
        "task_evolution": {"signals": config["online"]["signals"]},
    }


def generated(name, source=None):
    return {
        "task_id": name,
        "source_task_id": source or cfg()["feedback_task_ids"][0],
        "instruction": "Synthetic public task",
    }


def test_cross_round_queue_and_generated_frequency():
    originals = cfg()["feedback_task_ids"]
    pool = [generated(str(i)) for i in range(9)]
    q = TaskQueue(originals, 42)
    rows = [
        q.take(r, s, pool, [t["task_id"] for t in pool])
        for r in range(5)
        for s in range(40)
    ]
    assert len(rows) == 200
    old = [r["task_id"] for r in rows if r["source"] == "original"]
    assert len(old) == 155 and set(old[:100]) == set(originals)
    for r in range(5):
        selected = [
            t["generated_task"]["task_id"]
            for t in rows[r * 40 : (r + 1) * 40]
            if "generated_task" in t
        ]
        assert len(selected) == len(set(selected)) == 9
    assert sum(t.get("generated_task", {}).get("task_id") == "0" for t in rows) == 5


def test_priority_new_task_live_admission_and_resume_rng():
    originals = cfg()["feedback_task_ids"]
    pool = [generated("old")]
    q = TaskQueue(originals, 2026)
    q.take(0, 0, pool, ["old"])
    assert q.take(0, 1, pool, ["old"])["generated_task"]["task_id"] == "old"
    pool.append(generated("new"))
    q.take(0, 2, pool, ["old"])
    assert q.take(0, 3, pool, ["old"])["generated_task"]["task_id"] == "new"
    restored = TaskQueue(originals, 0, json.loads(json.dumps(q.export())))
    assert [q.take(0, i, pool, ["old"]) for i in range(4, 40)] == [
        restored.take(0, i, pool, ["old"]) for i in range(4, 40)
    ]


def test_empty_pool_and_fifty_percent_ceiling():
    originals = cfg()["feedback_task_ids"]
    q = TaskQueue(originals, 1)
    assert all(q.take(0, i, [], [])["source"] == "original" for i in range(40))
    q = TaskQueue(originals, 1)
    pool = [generated(str(i)) for i in range(100)]
    assert sum(q.take(0, i, pool, [])["source"] != "original" for i in range(40)) == 20


def make_episode(tmp_path, label, outcome, task=None, policy="p", generated_id=None):
    path = tmp_path / label
    messages = [
        {
            "role": "user",
            "content": json.dumps({"instruction": "Synthetic public train task"}),
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "x",
                    "function": {"name": "gmail__show_emails", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "x", "content": "[]"},
    ]
    write_json(path / "history.json", {"messages": messages})
    return {
        "task_id": task or cfg()["feedback_task_ids"][0],
        "generated_task_id": generated_id,
        "policy": {"checkpoint_id": policy},
        "initial_state_hash": "clean",
        "success": outcome,
        "classification": "infrastructure_error"
        if outcome is None
        else "success"
        if outcome
        else "natural_failure",
        "directory": str(path),
        "termination": "completion_claim",
        "usage": {},
        "costs": {},
        "wall_seconds": 1,
    }


def make_group(tmp_path, label, outcomes, **kwargs):
    return [
        make_episode(tmp_path, label + str(i), s, **kwargs)
        for i, s in enumerate(outcomes)
    ]


def test_complete_group_forgetting_uses_only_prior_groups(tmp_path):
    ledger = GroupFeedback({"window": 8, "minimum_observations": 20, "rare_percent": 5})
    first = ledger.observe(0, make_group(tmp_path, "a", [True, False, False, False]))
    assert first["signals"] == ["boundary"] and len(first["episodes"]) == 4
    second = ledger.observe(1, make_group(tmp_path, "b", [False] * 4, policy="new"))
    assert set(second["signals"]) == {"all_failed", "forgetting"}
    assert second["prior_success_groups"][0]["group_id"] == 0
    restored = GroupFeedback(ledger.settings, json.loads(json.dumps(ledger.state)))
    assert restored.state == ledger.state
    with pytest.raises(ValueError, match="consumed"):
        restored.observe(1, [])


@pytest.mark.parametrize("damage", ["unknown", "policy", "state", "generated"])
def test_invalid_group_not_business_failure(tmp_path, damage):
    rows = make_group(tmp_path, "a", [False] * 4)
    if damage == "unknown":
        rows[0].update(success=None, classification="infrastructure_error")
    elif damage == "policy":
        rows[0]["policy"] = {"checkpoint_id": "different"}
    elif damage == "state":
        rows[0]["initial_state_hash"] = "different"
    else:
        rows[0]["generated_task_id"] = "different"
    ledger = GroupFeedback(cfg()["task_evolution"]["signals"])
    event = ledger.observe(0, rows)
    assert not event["complete"] and not event["signals"] and not ledger.state["recent"]


def test_window_selection_latest_complete_and_all_failed_reservation():
    def event(key, labels, index):
        return {"task_key": key, "signals": labels, "complete": True, "group_id": index}

    window = [
        event("a", ["boundary", "forgetting"], 0),
        event("b", ["boundary"], 1),
        event("c", ["boundary"], 2),
        event("d", ["forgetting"], 3),
        event("z", ["all_failed"], 4),
        event("r", ["rare"], 5),
        event("a", ["boundary"], 6),
        {**event("z", [], 7), "complete": False},
    ]
    chosen = select_contexts(window)
    assert [e["task_key"] for e in chosen] == ["z", "a", "b", "c"]
    assert chosen[0]["selection_reason"] == "reserved_all_failed"
    assert chosen[1]["group_id"] == 6
    assert len(select_contexts([{**e, "complete": False} for e in window])) == 0


def test_generated_feedback_identity_and_train_only(tmp_path):
    ledger = GroupFeedback(cfg()["task_evolution"]["signals"])
    first = ledger.observe(
        0, make_group(tmp_path, "a", [True] * 4, generated_id="new_A")
    )
    second = ledger.observe(
        1, make_group(tmp_path, "b", [False] * 4, generated_id="new_B")
    )
    assert first["task_key"] == "new_A" and "forgetting" not in second["signals"]
    with pytest.raises(ValueError, match="scope"):
        ledger.observe(
            2, make_group(tmp_path, "c", [False] * 4, task=plan()["dev24"][0])
        )
