"""Synthetic segment scheduling, no pretrained model, Teacher or AppWorld calls."""

import copy

import pytest

from recoveragent.io import read_json, write_json
from recoveragent.s2_segment import Segment, update_request
from recoveragent.s2_state import protocol_identity
from recoveragent.demonstrations import sha256
from test_s2_experiment import cfg, FakeRuntime, make_group


def segment(tmp_path, *, groups=80, updates=20):
    from recoveragent.s2_schedule import TaskQueue

    config = cfg()
    queue = TaskQueue(config["feedback_task_ids"], config["s2"]["seed"])
    for slot in range(40):
        queue.take(0, slot, [], [])
    config["segment"] = {
        "parent_groups": 40,
        "parent_updates": 14,
        "max_new_groups": groups,
        "max_new_updates": updates,
    }
    runtime = FakeRuntime(config)
    parent = {
        "protocols": protocol_identity(),
        "actor": "parent14",
        "optimizer": "optim",
        "optimizer_sha256": "hash",
        "optimizer_steps": 14,
        "groups": ["read_only_parent"] * 40,
        "events": [None] * 40,
        "pool": [],
        "initial_ids": [],
        "lineage": {},
        "queue": queue.export(),
        "feedback_state": None,
        "feedback_windows": {},
        "pending": None,
    }
    parent_path = tmp_path / "parent.json"
    write_json(parent_path, parent)
    deployment = {
        "s0": "S0",
        "segment_parent": {
            "state": str(parent_path),
            "state_sha256": sha256(parent_path),
            "optimizer_sha256": "hash",
        },
    }
    bundle = {"pool": [], "archive_sha256": "synthetic"}
    return Segment(config, bundle, deployment, tmp_path / "new", "evolve", runtime)


def add(run, outcomes, *, policy=None):
    i = len(run.state["groups"])
    rows = make_group(run.output, str(i), outcomes, policy=policy or run.state["actor"])
    event = {"complete": all(x is not None for x in outcomes)}
    path = run.output / "groups" / str(i) / "group.json"
    write_json(path, {"index": i, "episodes": rows, "event": event})
    run.state["groups"].append(str(path))
    run.state["events"].append(event)
    run.save()


def test_effective_accumulation_retains_feedback_and_resume(tmp_path):
    run = segment(tmp_path)
    add(run, [True] * 4)
    add(run, [True, False, False, False])
    run.update()
    assert run.state["actor"] == "parent14"
    assert run.state["segment"]["pending_effective"] == [41]
    assert run.state["updated_through"] == 41  # never clean group 41
    add(run, [False] * 4)
    add(run, [None, False, False, False])
    resumed = Segment(
        run.cfg, run.bundle, run.deployment, run.output, "evolve", run.runtime
    )
    resumed.update()
    assert resumed.state["segment"]["pending_effective"] == [41]
    add(resumed, [False, True, True, False])
    resumed.update()
    assert resumed.state["optimizer_steps"] == 15
    assert resumed.state["segment"]["consumed_groups"] == [41, 44]
    assert resumed.state["segment"]["pending_effective"] == []
    assert len(resumed.state["events"]) == 45
    assert read_json(resumed.state["updates"][0])["group_ids"] == [41, 44]
    again = Segment(
        run.cfg, run.bundle, run.deployment, run.output, "evolve", run.runtime
    )
    again.update()
    assert len(again.state["updates"]) == 1
    assert again.state["actor"] == "actor_15"


def test_no_round_flush_tail_and_segment_caps(tmp_path):
    run = segment(tmp_path, groups=41, updates=1)
    for _ in range(39):
        add(run, [False] * 4)
    add(run, [True, False, True, False])
    run.update()
    assert len(run.state["groups"]) == 80
    assert run.state["segment"]["pending_effective"] == [79]
    assert run.stop_reason() is None
    add(run, [False] * 4)
    run.update()
    assert run.stop_reason() == "new_group_limit"
    run.update(tail=True)
    result = read_json(run.state["updates"][0])
    assert result["tail_batch"] and result["group_ids"] == [79]
    assert run.state["optimizer_steps"] == 15
    assert run.stop_reason() == "new_update_limit"
    run.update(tail=True)
    assert len(run.state["updates"]) == 1


def test_no_actor_mixing_and_bound_parent(tmp_path):
    run = segment(tmp_path)
    add(run, [True, False, False, False], policy="stale")
    with pytest.raises(ValueError, match="old actor"):
        run.update()
    changed = copy.deepcopy(run.deployment)
    changed["segment_parent"]["state_sha256"] = "changed"
    with pytest.raises(ValueError, match="inputs"):
        Segment(run.cfg, run.bundle, changed, run.output, "evolve", run.runtime)


def test_request_exact_pending_group_ids_and_one_tail(tmp_path):
    run = segment(tmp_path)
    add(run, [False, True, True, False])
    run.collect_effective()
    assert update_request(run.state, run.deployment) is None
    request = update_request(run.state, run.deployment, tail=True)
    assert request["group_ids"] == [40]
    assert request["audit_behavior"] and request["segment_parent_updates"] == 14


@pytest.mark.parametrize(
    "group_cap,update_cap,expected_groups,expected_updates",
    [(3, 20, 3, 2), (80, 1, 2, 1)],
)
def test_actual_segment_loop_caps_and_next_actor(
    tmp_path, group_cap, update_cap, expected_groups, expected_updates
):
    run = segment(tmp_path, groups=group_cap, updates=update_cap)
    feedback_at = []
    run.feedback = lambda: feedback_at.append(len(run.state["groups"]))
    run.train_segment()
    assert len(run.state["groups"]) == 40 + expected_groups
    assert run.state["optimizer_steps"] == 14 + expected_updates
    assert {p for p, *_ in run.runtime.calls[:8]} == {"parent14"}
    if expected_groups == 3:
        assert {p for p, *_ in run.runtime.calls[8:]} == {"actor_15"}
    assert run.state["round_evaluations"] == {}
    assert feedback_at == list(range(40, 40 + expected_groups))
    calls = len(run.runtime.calls)
    resumed = Segment(
        run.cfg, run.bundle, run.deployment, run.output, "evolve", run.runtime
    )
    resumed.train_segment()
    assert len(run.runtime.calls) == calls


def test_latest_generation_config_and_historical_lock_preserved(monkeypatch):
    from recoveragent.v2_runtime import (
        S2_RUNTIME,
        S2_SEGMENT_EXECUTION,
        S2_GENERATION_FOCUS_EXECUTION,
        S2_APPEND_EXECUTION,
        verify_lock,
    )
    from recoveragent.s2_experiment import configuration

    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_APPEND_EXECUTION)
    configured = configuration()
    old = read_json(f"configs/{S2_GENERATION_FOCUS_EXECUTION}.json")
    new = read_json(f"configs/{S2_APPEND_EXECUTION}.json")
    for key in (
        "judge_text",
        "generation_settings",
        "generation_prompt_sha256",
        "generation_input_revision",
        "teacher_context",
    ):
        assert new[key] == old[key]
    assert (
        configured["grpo"]["learning_rate"] == 1e-5 and configured["grpo"]["beta"] == 0
    )
    assert configured["s2"]["student_trajectories_per_branch"] == 320
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GENERATION_FOCUS_EXECUTION)
    with pytest.raises(ValueError, match="source lock|source changed"):
        verify_lock(S2_RUNTIME)
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_SEGMENT_EXECUTION)
    with pytest.raises(ValueError, match="source lock|source changed"):
        verify_lock(S2_RUNTIME)


def test_inherited_pool_verifies_original_proof_and_same_public_protocol(
    tmp_path, monkeypatch
):
    from recoveragent import s2_segment
    from recoveragent.s2_segment import load_segment_bundle
    from recoveragent.s2_llm_pool import manifest, load
    from recoveragent.coevolve_llm import REVISION

    old = protocol_identity()
    folder = tmp_path / "pool"
    write_json(
        folder / "bundle.json",
        {"protocols": old, "scoring_revision": REVISION, "pool": [], "feedback": []},
    )
    manifest(folder, "llm_initial_pool_v1")
    state = tmp_path / "parent.json"
    write_json(state, {"protocols": old})
    deployment = {
        "initial_bundle": str(folder),
        "segment_parent": {"state": str(state), "state_sha256": sha256(state)},
    }
    current = {**old, "execution": "explicit_new_segment"}
    monkeypatch.setattr(s2_segment, "protocol_identity", lambda: current)
    assert load_segment_bundle(deployment)["pool"] == []
    assert load(folder)["pool"] == []
    old["reward"] = "incompatible_reward"
    state = tmp_path / "incompatible_parent.json"
    write_json(state, {"protocols": old})
    deployment["segment_parent"]["state"] = str(state)
    deployment["segment_parent"]["state_sha256"] = sha256(state)
    with pytest.raises(ValueError, match="protocol differs"):
        load_segment_bundle(deployment)
