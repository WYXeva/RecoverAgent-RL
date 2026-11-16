"""Synthetic scheduling/continuation checks, no model or paid Teacher calls."""

import json
from pathlib import Path

import pytest

from recoveragent.io import read_json, write_json
from recoveragent.s2_schedule import GroupFeedback, TaskQueue, select_contexts
from recoveragent.s2_experiment import Experiment, evaluate
from recoveragent.s2_reporting import best_round
from recoveragent.v2_tasks import context_for, plan


def cfg():
    return read_json("configs/v2_s2_static_evolve_20261116.json")


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


def test_test_split_explicit_final_evaluation_only():
    task = cfg()["test_task_ids"][0]
    with pytest.raises(ValueError):
        context_for(task)
    with pytest.raises(ValueError):
        context_for(task, final_test=True, training=True)
    assert context_for(task, final_test=True)["role"] == "test"


class FakeRuntime:
    def __init__(self, config, crash=None):
        self.cfg = config
        self.calls = []
        self.crash = crash
        self.deadline = 1e20

    def available(self, reserve=0):
        pass

    def stop(self):
        pass

    def cleanup(self, groups, state, output):
        pass

    def policy(self, actor):
        return {"checkpoint_id": actor}

    def rollout(self, actor, task, directory, seed, **kwargs):
        self.calls.append(
            (actor, task.get("generated_task", {}).get("task_id"), seed, str(directory))
        )
        if self.crash == len(self.calls):
            raise KeyboardInterrupt("synthetic power interruption")
        result = make_episode(
            Path(directory).parent,
            Path(directory).name,
            bool(seed % 2),
            task=task["task_id"],
            policy=actor,
            generated_id=task.get("generated_task", {}).get("task_id"),
        )
        write_json(
            Path(directory) / "session/requests/001.request.json",
            {
                "messages": [{"role": "user", "content": task["task_id"]}],
                "tools": [],
                "input_token_ids": [1, 2],
                "max_input_tokens": 65536,
                "max_new_tokens": 4,
                "temperature": 0.8,
                "top_p": 1.0,
                "seed": seed,
            },
        )
        return {**result, "seed": seed}

    def update(self, request, directory):
        assert request["s0"] == "S0"
        return {
            "updated": True,
            "actor": "actor_" + str(request["optimizer_steps"] + 1),
            "optimizer": "optim",
            "optimizer_sha256": "hash",
            "optimizer_steps": request["optimizer_steps"] + 1,
            "contributing_generated_task_ids": sorted(
                {
                    e["generated_task_id"]
                    for g in request["groups"]
                    for e in g
                    if e["generated_task_id"]
                }
            ),
        }


def experiment(tmp_path, runtime):
    config = runtime.cfg
    bundle = {"pool": [generated("new_A")], "archive_sha256": "source", "feedback": []}
    return Experiment(config, bundle, {"s0": "S0"}, tmp_path, "static", runtime)


def test_online_weight_handoff_and_committed_update_resume(tmp_path):
    runtime = FakeRuntime(cfg())
    run = experiment(tmp_path, runtime)
    run.group()
    run.group()
    run.update()
    assert {x[0] for x in runtime.calls} == {"S0"}
    resumed = experiment(tmp_path, runtime)
    resumed.update()
    assert len(resumed.state["updates"]) == 1
    resumed.group()
    assert {x[0] for x in runtime.calls[8:]} == {"actor_1"}
    assert resumed.state["optimizer_steps"] == 1


def test_resume_partial_group_does_not_repeat_consumed_trajectory(tmp_path):
    runtime = FakeRuntime(cfg(), crash=2)
    run = experiment(tmp_path, runtime)
    with pytest.raises(KeyboardInterrupt):
        run.group()
    new_runtime = FakeRuntime(cfg())
    resumed = experiment(tmp_path, new_runtime)
    resumed.group()
    assert len(new_runtime.calls) == 2
    group = read_json(resumed.state["groups"][0])
    assert group["episodes"][1]["success"] is None and not group["event"]["complete"]


def test_zero_updates_still_runs_all_five_rounds_and_reuses_generated_tasks(tmp_path):
    runtime = FakeRuntime(cfg())
    runtime.update = lambda request, directory: {
        "updated": False,
        "reason": "no_variation",
        "actor": request["actor"],
        "optimizer_steps": 0,
    }
    run = experiment(tmp_path, runtime)
    baseline = evaluate(
        FakeRuntime(cfg()), "S0", cfg()["dev_task_ids"], tmp_path / "baseline"
    )
    state = run.run(baseline)
    assert len(state["groups"]) == 200 and len(state["updates"]) == 100
    assert state["actor"] == "S0" and state["optimizer_steps"] == 0
    assert len(runtime.calls) == 800 + 5 * 24
    assert sum(gid == "new_A" for _, gid, _, _ in runtime.calls) == 20
    assert best_round(state["round_evaluations"])["round"] == 1


def test_best_dev_tie_earlier_round_unknown_not_success():
    rows = {
        str(i): {
            "round": i + 1,
            "counts": {"success": 8 if i in (1, 3) else 7, "unknown": 1},
        }
        for i in range(5)
    }
    assert best_round(rows)["round"] == 2


def test_evaluation_resume_no_retry(tmp_path):
    runtime = FakeRuntime(cfg(), crash=2)
    tasks = cfg()["dev_task_ids"][:3]
    with pytest.raises(KeyboardInterrupt):
        evaluate(runtime, "S0", tasks, tmp_path)
    new = FakeRuntime(cfg())
    rows = evaluate(new, "S0", tasks, tmp_path)
    assert len(new.calls) == 1 and rows[1]["success"] is None


def test_final_test_selects_best_dev_and_audits_shared_inputs(tmp_path):
    from recoveragent.s2_experiment import final_evaluation

    config = cfg()
    config["test_task_ids"] = config["test_task_ids"][:3]
    write_json(tmp_path / "experiment.json", {"config": config, "s0": "S0"})
    for branch in ("static", "evolve"):
        write_json(
            tmp_path / branch / "state.json",
            {
                "round_evaluations": {
                    str(i): {
                        "round": i + 1,
                        "actor": branch + str(i),
                        "counts": {"success": 8 if i in (1, 3) else 7},
                    }
                    for i in range(5)
                }
            },
        )
    runtime = FakeRuntime(config)
    final_evaluation(config, {"s0": "S0"}, tmp_path, runtime)
    assert len(runtime.calls) == 9
    summary = read_json(tmp_path / "test_summary.json")
    assert summary["static"]["actor"] == "static1"
    assert summary["evolve"]["actor"] == "evolve1"
    assert all(
        r["same_input"] for r in read_json(tmp_path / "test/static/paired_inputs.json")
    )
    final_evaluation(config, {"s0": "S0"}, tmp_path, runtime)
    assert len(runtime.calls) == 9
