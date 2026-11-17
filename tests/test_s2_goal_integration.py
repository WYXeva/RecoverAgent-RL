"""Local wiring/continuation tests with synthetic backend and semantic replies."""

import copy
import time
from pathlib import Path

import pytest

from recoveragent.io import digest, write_json, read_json
from recoveragent import s2_backend, s2_reward, s2_fresh_probe
from recoveragent.v2_runtime import S2_GOAL_REPAIR_EXECUTION
from recoveragent.s2_state import protocol_identity
from test_s2_experiment import cfg
from test_public_goal_reward import fixture
from recoveragent.coevolve_goal import objective


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {"conditions": [None]},
        {"conditions": ["body"]},
        {"conditions": {"body": True}},
    ],
)
def test_real_rollout_boundary_finalizes_student_semantics_once(
    tmp_path, monkeypatch, malformed
):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GOAL_REPAIR_EXECUTION)
    config = cfg()
    runtime = s2_backend.BackendRuntime(
        config,
        {
            "deadline_epoch": time.time() + 3600,
            "appworld_root": "synthetic",
            "worker_python": "synthetic",
        },
        tmp_path,
    )
    runtime.tokenizer = object()
    monkeypatch.setattr(runtime, "serve", lambda _: object())
    monkeypatch.setattr(runtime, "policy", lambda _: {"checkpoint_id": "s0"})
    task, state = fixture()
    task["success_conditions"]["semantic"] = [
        {"id": "body", "requirement": "A useful body", "actions": ["mail__send"]}
    ]
    state["actions"] = [
        {
            "name": "mail__send",
            "args": {"body": "Equivalent actual Student text"},
            "result": {"sent": True},
            "failed": False,
            "evidence_ref": {"rollout_id": "actual", "action_index": 3},
        }
    ]
    result = {
        "task_id": config["feedback_task_ids"][0],
        "success": None,
        "reward_valid": False,
        "fault": None,
        "evaluation": objective(task, state),
        "policy": {"checkpoint_id": "s0"},
    }
    requests, judged = [], []

    def episode(request, *a, **kw):
        requests.append(request)
        write_json(Path(request["output"]) / "result.json", result)
        return copy.deepcopy(result)

    monkeypatch.setattr(s2_backend, "episode", episode)

    def score(runtime, raw, directory):
        def judge(content):
            judged.append(content)
            if malformed is not None:
                return malformed
            return {
                "conditions": [
                    {
                        "id": "body",
                        "passed": True,
                        "reason": "synthetic equivalence",
                        "evidence_refs": [{"rollout_id": "actual", "action_index": 3}],
                    }
                ]
            }

        return s2_reward.finalize(raw, judge)

    monkeypatch.setattr(s2_reward, "score", score)
    output = tmp_path / "episode"
    final = runtime.rollout(
        "s0", {"task_id": result["task_id"], "generated_task": task}, output, 7
    )
    assert final["success"] is (True if malformed is None else None)
    assert final["reward_valid"] is (malformed is None)
    if malformed is not None:
        assert final["classification"] == "scoring_gap"
        assert final["scoring_fault"]["type"] == "ScoringGap"
    assert requests[0]["completion_contract_revision"] == "completion_submission_v2"
    assert (
        judged[0]["conditions"][0]["evidence"][0]["args"]["body"]
        == "Equivalent actual Student text"
    )
    assert (
        read_json(output / "result.json")["success"] is None
    )  # immutable raw evaluation
    assert runtime.finalize_reward(result, output) == {
        k: v for k, v in final.items() if k not in {"directory", "seed"}
    }
    assert len(judged) == 1
    with pytest.raises(ValueError, match="raw episode"):
        runtime.finalize_reward({**result, "task_id": "changed"}, output)


def test_fresh16_resume_never_reexecutes_claimed_slot(tmp_path, monkeypatch):
    from recoveragent import s2_experiment, v2_training

    from recoveragent.v2_runtime import S2_JUDGE_REFERENCE_EXECUTION

    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_JUDGE_REFERENCE_EXECUTION)
    config = cfg()
    plan = {
        "tasks": [
            {"task_id": t, "coverage": str(i)}
            for i, t in enumerate(config["feedback_task_ids"][:4])
        ]
    }
    frozen = {
        "protocols": protocol_identity(),
        "source_lock": {"synthetic": True},
        "plan_sha256": digest(plan),
        "plan": plan,
        "seed": 100,
    }
    write_json(tmp_path / "frozen.json", frozen)
    deployment = tmp_path / "deployment.json"
    write_json(deployment, {"s0": "synthetic_S0"})
    monkeypatch.setattr(s2_fresh_probe, "verify_lock", lambda *_: {"synthetic": True})
    monkeypatch.setattr(s2_experiment, "configuration", lambda: config)
    monkeypatch.setattr(
        v2_training,
        "verify_checkpoint",
        lambda *_: {"adapter_sha256_after": config["s0"]["adapter_sha256"]},
    )
    attempts = []
    interrupted = [False]

    class Runtime:
        def __init__(self, *a):
            pass

        def available(self, *a):
            pass

        def stop(self):
            pass

        def policy(self, actor):
            return {"checkpoint_id": actor}

        def rollout(self, actor, task, directory, seed):
            attempts.append(str(directory))
            if len(attempts) == 3 and not interrupted[0]:
                interrupted[0] = True
                raise TimeoutError("synthetic interruption")
            return {
                "task_id": task["task_id"],
                "generated_task_id": None,
                "policy": self.policy(actor),
                "initial_state_hash": task["task_id"],
                "success": False,
                "reward_valid": True,
                "usage": {"model_requests": 1, "generated_tokens": 3},
                "wall_seconds": 1,
            }

    monkeypatch.setattr(s2_backend, "BackendRuntime", Runtime)
    with pytest.raises(TimeoutError):
        s2_fresh_probe.run(tmp_path, deployment)
    result = s2_fresh_probe.run(tmp_path, deployment)
    assert (
        result["attempts"] == 16 and result["invalid"] == 1 and result["failure"] == 15
    )
    assert len(attempts) == len(set(attempts)) == 16
    again = s2_fresh_probe.run(tmp_path, deployment)
    assert again == result and len(attempts) == 16


def test_experiment_protocol_binding_rejects_old_actor_and_allows_same_resume(
    tmp_path, monkeypatch
):
    from test_s2_experiment import experiment, FakeRuntime
    from recoveragent.v2_runtime import S2_GOAL_EXECUTION

    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GOAL_EXECUTION)
    runtime = FakeRuntime(cfg())
    previous = experiment(tmp_path / "old", runtime)
    previous.state.update(actor="old_reward_actor", optimizer_steps=19)
    previous.save()
    raw = (tmp_path / "old/state.json").read_bytes()
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GOAL_REPAIR_EXECUTION)
    with pytest.raises(ValueError, match="protocols"):
        experiment(tmp_path / "old", runtime)
    assert not runtime.calls and (tmp_path / "old/state.json").read_bytes() == raw
    current = experiment(tmp_path / "new", runtime)
    current.state["optimizer_steps"] = 2
    current.save()
    resumed = experiment(tmp_path / "new", runtime)
    assert resumed.state == current.state
    assert resumed.state["actor"] == "S0"


def test_evaluation_protocol_binding_cannot_reuse_old_receipts(tmp_path, monkeypatch):
    from test_s2_experiment import FakeRuntime
    from recoveragent.s2_experiment import evaluate
    from recoveragent.v2_runtime import S2_GOAL_EXECUTION

    runtime = FakeRuntime(cfg())
    task = cfg()["dev_task_ids"][0]
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GOAL_EXECUTION)
    old = evaluate(runtime, "S0", [task], tmp_path / "old")
    calls = len(runtime.calls)
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GOAL_REPAIR_EXECUTION)
    with pytest.raises(ValueError, match="evidence changed"):
        evaluate(runtime, "S0", [task], tmp_path / "old")
    assert len(runtime.calls) == calls
    new = evaluate(runtime, "S0", [task], tmp_path / "new")
    assert evaluate(runtime, "S0", [task], tmp_path / "new") == new
    assert len(runtime.calls) == calls + 1
    # A copied old receipt with no binding must not be blessed by a new binding.
    write_json(tmp_path / "unbound" / f"{task}.receipt.json", old[0])
    with pytest.raises(ValueError, match="Unbound historical"):
        evaluate(runtime, "S0", [task], tmp_path / "unbound")
