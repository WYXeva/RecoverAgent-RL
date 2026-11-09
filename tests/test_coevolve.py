"""Synthetic evidence tests; no model calls or real AppWorld score claims."""

import copy
from types import SimpleNamespace

import pytest

from recoveragent import coevolve_tasks as tasks
from recoveragent.coevolve_generation import Evolution
from recoveragent.coevolve_signals import SignalLedger, interaction_triplets
from recoveragent.io import digest, read_json, write_json
from recoveragent.v2_round import config
from recoveragent.v2_resume import completed_sft_recipe
from recoveragent.v2_tasks import ROOT, plan


def history(names=("gmail__login", "gmail__show_category_sizes", "gmail__show_emails")):
    messages = []
    for i, name in enumerate(names):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": str(i),
                            "type": "function",
                            "function": {"name": name, "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": str(i), "content": "{}"},
            ]
        )
    return messages


def episode(tmp_path, name, success, *, task=None, policy="s1", messages=None):
    directory = tmp_path / name
    write_json(directory / "history.json", {"messages": messages or history()})
    return {
        "task_id": task or plan()["feedback20"][0],
        "generated_task_id": None,
        "directory": str(directory),
        "policy": policy,
        "initial_state_hash": "initial",
        "success": success,
        "classification": "success" if success else "natural_failure",
    }


def test_all_three_signals_and_no_intragroup_false_forgetting(tmp_path):
    ledger = SignalLedger(window=2, minimum_observations=1, rare_percent=90)
    group = [episode(tmp_path, str(i), bool(i % 2)) for i in range(4)]
    events = ledger.observe(group, group=True)
    assert all("boundary" in e["signals"] and "rare" in e["signals"] for e in events)
    assert all("forgetting" not in e["signals"] for e in events)
    event = ledger.observe([episode(tmp_path, "later", False, policy="s2")])[0]
    assert "forgetting" in event["signals"]
    assert event["history_sha256"] == digest(history())
    with pytest.raises(ValueError, match="already consumed"):
        ledger.observe([group[0]])


def test_signal_scope_and_group_consistency(tmp_path):
    ledger = SignalLedger(window=2, minimum_observations=20, rare_percent=5)
    unknown = episode(tmp_path, "unknown", None)
    assert ledger.observe([unknown]) == []
    assert ledger.pattern_total == 0
    with pytest.raises(ValueError):
        ledger.observe([episode(tmp_path, "dev", False, task=plan()["dev24"][0])])
    a, b = episode(tmp_path, "a", True), episode(tmp_path, "b", False, policy="s2")
    with pytest.raises(ValueError, match="one task"):
        ledger.observe([a, b], group=True)
    with pytest.raises(ValueError, match="Incomplete"):
        interaction_triplets(history()[:-1], "bad")


def make_candidate():
    record = {
        "task_id": plan()["feedback20"][0],
        "trajectory_id": "real_explore",
        "messages": history(),
    }
    proposal = {
        "query": "Count unread threads in inbox and spam. Return one integer.",
        "action_sequence": "Authenticate, inspect categories, sum the two counts.",
        "completion_criterion": "Exact sum, no email mutations.",
        "evidence_refs": [
            {"rollout_id": "real_explore", "action_index": i} for i in range(3)
        ],
    }
    return (
        tasks.candidate(proposal, [record], {"summary": "synthetic"}),
        record,
        proposal,
    )


def reference(task):
    return {
        "status": "valid",
        "initial_state_hash": "initial",
        "trajectory_id": "solver",
        "messages": history(),
        "coevolve_terminal": {
            "status": "success",
            "instruction": task["instruction"],
            "answer": "7",
            "business_sha256": "business",
        },
    }


def test_abstraction_requires_executed_evidence_and_train_source():
    task, record, proposal = make_candidate()
    assert task["task_id"].startswith("coevolve_")
    proposal["evidence_refs"][0]["action_index"] = 99
    with pytest.raises(ValueError, match="invented"):
        tasks.candidate(proposal, [record], {})
    record["task_id"] = plan()["dev24"][0]
    with pytest.raises(ValueError):
        tasks.candidate(proposal, [record], {})


def test_reference_requires_semantic_and_real_completion_and_four_checks(monkeypatch):
    task, _, _ = make_candidate()
    ref = reference(task)
    with pytest.raises(ValueError, match="semantic"):
        tasks.attach_reference(task, ref, {"success": "true", "reason": "claim"})
    task = tasks.attach_reference(task, ref, {"success": True, "reason": "observed"})
    current = {"instruction": task["instruction"], "status": "success", "answer": "7"}
    adapter = SimpleNamespace(
        world=SimpleNamespace(
            requester=SimpleNamespace(request=lambda *a, **k: current)
        )
    )
    monkeypatch.setattr(tasks, "business_hash", lambda a: "business")
    assert tasks.evaluate(adapter, task)["success"]
    current["answer"] = "8"
    assert not tasks.evaluate(adapter, task)["success"]
    checks = [
        {
            "mode": mode,
            "initial_state_hash": "initial",
            "evaluation": {"success": mode == "positive"},
        }
        for mode in ("positive", "noop", "omit_completion", "wrong_answer")
    ]
    accepted = tasks.accept(task, checks)
    tasks.validate_task(accepted, task["source_task_id"])
    damaged = copy.deepcopy(accepted)
    damaged["validation"]["results"][1]["evaluation"]["success"] = True
    with pytest.raises(ValueError):
        tasks.validate_task(damaged, task["source_task_id"])
    damaged = copy.deepcopy(accepted)
    damaged["execution_contract"]["expected_answer"] = "8"
    with pytest.raises(ValueError):
        tasks.validate_task(damaged, task["source_task_id"])


def test_end_to_end_controller_and_online_pool(tmp_path, monkeypatch):
    cfg = config()
    cfg["task_evolution"]["signals"] = {
        "window": 2,
        "minimum_observations": 10000,
        "rare_percent": 5,
    }
    summary = {"teacher_usage_priced_cny": 3.0}

    def isolated(python, command, payload, directory, deadline):
        assert command == "generator-check"
        write_json(
            directory / "validation.json",
            {
                "mode": payload["mode"],
                "initial_state_hash": "initial",
                "evaluation": {"success": payload["mode"] == "positive"},
            },
        )

    engine = Evolution(
        cfg,
        {},
        {
            "worker_python": "synthetic",
            "appworld_root": "synthetic",
            "teacher_budget_cny": 10,
        },
        tmp_path / "evolve",
        summary,
        1e20,
        isolated,
    )
    executions, text_calls = [], []

    def execute(source, directory, payload):
        executions.append((str(directory), payload))
        if payload["mode"] == "validation":
            record = reference(payload["task"])
        else:
            record = {
                "task_id": source,
                "trajectory_id": directory.name,
                "messages": history(),
            }
        write_json(directory / "trajectory.json", record)
        return record

    def text(source, directory, system, content):
        text_calls.append(content)
        if directory.name == "abstraction":
            return {
                "tasks": [
                    {
                        "query": "Count a new category and return an integer.",
                        "action_sequence": "Three real actions",
                        "completion_criterion": "exact count",
                        "evidence_refs": [
                            {
                                "rollout_id": r["rollout_id"],
                                "action_index": r["action_index"],
                            }
                            for r in content["interaction_triplets"][:3]
                        ],
                    }
                ]
            }
        return {"success": True, "reason": "synthetic verified evidence"}

    monkeypatch.setattr(engine, "execute", execute)
    monkeypatch.setattr(engine, "text", text)
    engine.initial([episode(tmp_path, "feedback", False)])
    assert len(executions) == 3  # Two independent explorations plus one fresh solver.
    assert len({p for p, _ in executions}) == 3
    assert len(engine.pool) == 1 and summary["coevolve"]["accepted"] == 1
    original = {"source": "original", "task_id": plan()["feedback20"][0]}
    assert engine.task_at(0, original)["generated_task"] == engine.pool[0]
    assert engine.task_at(1, original) == original
    newer = {**engine.pool[0], "task_id": "synthetic_newer"}
    engine.pool.append(newer)
    assert engine.task_at(4, original)["generated_task"] == newer
    assert "execution_contract" not in tasks.exploration_instruction(
        {"mode": "validation", "task": engine.pool[0]}
    )
    # Boundary feedback triggers a subsequent cycle without replaying old groups.
    labels = []
    monkeypatch.setattr(engine, "evolve", labels.append)
    group = [episode(tmp_path, "group" + str(i), bool(i % 2)) for i in range(4)]
    engine.after_batch(0, [group])
    assert labels == ["batch_00"]
    assert any("boundary" in e["signals"] for e in engine.signals.events)


def test_unknown_text_usage_is_reserved_without_retry(tmp_path, monkeypatch):
    from recoveragent import coevolve_generation as module

    calls = []

    class Policy:
        def __init__(self, *args):
            pass

        def build_text_request(self, *args):
            return {"max_tokens": 4}

        def transport(self, request):
            calls.append(request)
            raise TimeoutError("synthetic")

    monkeypatch.setattr(module, "TeacherPolicy", Policy)
    monkeypatch.setattr(
        module,
        "configuration",
        lambda *a, **k: {
            "teacher": {
                "pricing": {"input_per_million": 2, "output_per_million": 8},
                "billing_context_tokens": 100,
            }
        },
    )
    monkeypatch.setattr(module, "read_credentials", lambda *a: {})
    summary = {"teacher_usage_priced_cny": 0.0}
    engine = Evolution(
        config(),
        {},
        {"teacher_budget_cny": 10, "appworld_root": "synthetic"},
        tmp_path,
        summary,
        1e20,
        None,
    )
    with pytest.raises(RuntimeError, match="unknown"):
        engine.text(plan()["feedback20"][0], tmp_path / "text", "synthetic", {})
    assert len(calls) == 1 and summary["teacher_usage_priced_cny"] > 0
    assert read_json(tmp_path / "text/unknown_usage.json")["automatic_retries"] == 0


def test_sft_handoff_allows_only_explicit_future_changes():
    old = read_json(ROOT / "configs/v2_round1_feedback15_20261109.json")
    current = config()
    normalized = completed_sft_recipe(current, old)
    for key in old.keys() - {"format", "source_lock", "gpu_wall_seconds"}:
        assert normalized[key] == old[key]
    assert "task_evolution" not in normalized
    current["recovery_sft"]["learning_rate"] *= 2
    assert completed_sft_recipe(current, old)["recovery_sft"] != old["recovery_sft"]
    assert current["grpo"]["beta"] == 0.001
    assert current["grpo"]["groups"] == 16 and current["dev_tasks"] == 24
