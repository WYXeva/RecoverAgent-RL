"""CPU checks for uncapped sampling and exact SFT-task exclusion."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from recoveragent.codeact import experiment
from recoveragent.io import read_json, write_json
from recoveragent.s2_schedule import TaskQueue, select_contexts
from recoveragent.v2_tasks import ROOT, plan


def test_completion_bounded_run_keeps_old_deadlines(monkeypatch):
    monkeypatch.setattr(experiment, "epoch", lambda: 100)
    assert experiment.within_deadline({"deadline": None})
    assert experiment.within_deadline({"deadline": 101})
    assert not experiment.within_deadline({"deadline": 100})
    timeouts = []
    worker = SimpleNamespace(wait=lambda **kw: timeouts.append(kw["timeout"]) or 0)
    experiment.wait_process(worker, None)
    experiment.wait_process(worker, 105)
    assert timeouts == [None, 5]
    inherited = experiment.InheritedGeneration(
        {"pid": 1, "identity": "synthetic"}, "/unused"
    )

    def done():
        inherited.returncode = 0
        return 0

    monkeypatch.setattr(inherited, "poll", done)
    experiment.wait_process(inherited, None)


def test_queue_migration_keeps_generated_slots_and_resume():
    old = ["sft", "seen", "old_unseen"]
    q = TaskQueue(old, 42)
    q.take(0, 0, [], [])
    before = q.export()
    used = before["queue"][: before["cursor"]]
    q.reconfigure(["seen", "old_unseen", "new1", "new2"], used + ["seen"])
    assert q.state["queue"][: before["cursor"]] == used
    assert "sft" not in q.state["queue"][q.state["cursor"] :]
    assert q.state["round"] == before["round"]
    pool = [{"task_id": "generated", "source_task_id": "sft"}]
    # A new goal from the same environment remains a distinct generated task.
    assert q.take(0, 1, pool, [])["generated_task"]["task_id"] == "generated"
    next_original = q.take(0, 2, pool, [])["task_id"]
    assert next_original not in used + ["seen", "sft"]
    restored = TaskQueue(q.originals, 0, json.loads(json.dumps(q.export())))
    snapshot = restored.export()
    restored.reconfigure(q.originals, used)
    assert restored.export() == snapshot  # No second shuffle on resume.
    for r, slots in [(0, range(3, 40)), (1, range(40))]:
        assert [q.take(r, s, pool, []) for s in slots] == [
            restored.take(r, s, pool, []) for s in slots
        ]
    assert set(q.originals) == {"seen", "old_unseen", "new1", "new2"}
    with pytest.raises(ValueError, match="migration"):
        TaskQueue(old, 0, q.export())


def test_uncapped_sampling_feedback_and_update_stop():
    cfg = {
        "online": {"max_groups": None, "max_updates": 100, "feedback_every_groups": 8}
    }
    assert not experiment.sampling_limit_reached(cfg, 200)
    assert not experiment.sampling_limit_reached(cfg, 10000)
    state = {"groups": [None] * 208, "optimizer_steps": 55}
    assert list(experiment.generation_windows(cfg, state))[-2:] == [200, 208]
    state["optimizer_steps"] = 100
    assert not list(experiment.generation_windows(cfg, state))
    cfg["online"]["max_groups"] = 200
    assert experiment.sampling_limit_reached(cfg, 200)
    state["optimizer_steps"] = 55
    assert list(experiment.generation_windows(cfg, state))[-1] == 192
    cfg["online"]["max_groups"] = 0
    with pytest.raises(ValueError, match="max_groups"):
        experiment.sampling_limit_reached(cfg, 0)


@pytest.mark.parametrize("eligible_count", [0, 1, 4, 8])
def test_excluded_originals_keep_eight_feedback_slots(eligible_count):
    window = [
        {
            "group_id": i,
            "task_id": f"t{i}",
            "task_key": f"t{i}",
            "complete": True,
            "signals": ["all_failed"] if i == 7 else ["boundary"],
        }
        for i in range(8)
    ]
    original = copy.deepcopy(window)
    allowed = {f"t{i}" for i in range(8 - eligible_count, 8)}
    selected = select_contexts(window, eligible_originals=allowed)
    assert len(selected) == min(eligible_count, 4)
    assert all(e["task_id"] in allowed for e in selected)
    if eligible_count:
        assert selected[0]["task_id"] == "t7"
    assert window == original
    # Different generated goal in an excluded source environment remains eligible.
    window[0].update(generated_task_id="new_goal", task_key="new_goal")
    assert [
        e["task_key"] for e in select_contexts(window, eligible_originals=set())
    ] == ["new_goal"]
    with pytest.raises(ValueError, match="eight completed"):
        select_contexts(window[:7], eligible_originals=allowed)


def test_retire_only_unconsumed_sft_original_preserve_raw(tmp_path):
    state = {"groups": [], "pending": [0, 1, 2], "consumed": [3]}
    for i, selection in enumerate(
        [
            {"task_id": "sft"},
            {"task_id": "sft", "generated_task": {"task_id": "new"}},
            {"task_id": "allowed"},
        ]
    ):
        p = tmp_path / f"{i}.json"
        write_json(p, {"selection": selection, "original_reward": [0, 1, 0, 1]})
        state["groups"].append(str(p))
    raw = [Path(p).read_bytes() for p in state["groups"]]
    experiment.retire_ineligible_pending(
        {"online": {"train_tasks": ["allowed"]}}, state
    )
    assert state["pending"] == [1, 2] and state["consumed"] == [3]
    assert state["retired_pending"][0]["group_id"] == 0
    assert [Path(p).read_bytes() for p in state["groups"]] == raw


def test_frozen_non_sft_pool_and_unchanged_execution_budgets():
    cfg = read_json(ROOT / "configs/codeact_grpo_coevolve_non_sft_20261120.json")
    previous = read_json(ROOT / "configs/codeact_grpo_coevolve_extend5_20261120.json")
    frozen = plan()
    train = {r["task_id"] for r in frozen["rows"] if r["role"] == "train"}
    allowed = set(cfg["online"]["train_tasks"])
    excluded = set(cfg["online"]["task_pool"]["excluded_sft_tasks"])
    assert len(train) == 510 and len(excluded) == 252 and len(allowed) == 258
    assert allowed == train - excluded and not allowed & excluded
    assert cfg["online"]["max_groups"] is None
    assert cfg["online"]["max_updates"] == 100
    for key in ["model", "lora", "student_inference", "limits", "teacher"]:
        assert cfg[key] == previous[key]
    for key in [
        "wall_seconds",
        "teacher_budget",
        "round_evaluation",
        "sampling_concurrency",
        "sampling_fallback",
    ]:
        assert cfg["online"][key] == previous["online"][key]
    altered = copy.deepcopy(cfg)
    altered["online"]["train_tasks"][0] = frozen["dev24"][0]
    with pytest.raises(ValueError, match="provenance"):
        experiment.validate_training_pool(altered, {})
