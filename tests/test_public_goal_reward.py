"""Synthetic goal states and semantic receipts; no model or AppWorld execution."""

import copy

import pytest

from recoveragent.coevolve_goal import (
    REVISION,
    ScoringGap,
    objective,
    finish_semantic,
    validate,
)
from recoveragent.completion_contract import render
from recoveragent.s2_reward import finalize
from recoveragent.v2_grpo import group_advantages
from recoveragent.reward_validity import recovery_failure


def literal(v):
    return {"literal": v}


def check(name, left, right, op="equal"):
    return {"id": name, "requirement": name, "left": left, "op": op, "right": right}


def fixture():
    spec = {
        "revision": REVISION,
        "output": {
            "required": True,
            "encoding": "json_string",
            "schema": {
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "integer"}},
                    "amount": {"type": "number"},
                },
                "required": ["ids", "amount"],
                "additionalProperties": False,
            },
        },
        "bindings": {"new": {"rows": {"table": "demo.Transfer", "phase": "created"}}},
        "checks": [
            check("amount", {"answer": ["amount"]}, literal(21.5)),
            check(
                "one_owned_new_object",
                {
                    "count": {
                        "rows": {
                            "table": "demo.Transfer",
                            "phase": "created",
                            "where": {"owner_id": 3, "amount": 21.5},
                        }
                    }
                },
                literal(1),
            ),
            check(
                "report_current_ids",
                {"answer": ["ids"]},
                {"values": {"from": {"binding": "new"}, "path": ["id"]}},
                "set_equal",
            ),
            check(
                "no_old_mutations",
                {"count": {"rows": {"table": "demo.Transfer", "phase": "changed"}}},
                literal(0),
            ),
            check(
                "no_deletions",
                {"count": {"rows": {"table": "demo.Transfer", "phase": "deleted"}}},
                literal(0),
            ),
            check("no_extra_transfers", {"count": {"binding": "new"}}, literal(1)),
        ],
    }
    task = {
        "instruction": render(
            "Transfer exactly 21.5 for me; report amount and new IDs. Do not change other transfers.",
            spec["output"],
        ),
        "success_conditions": spec,
    }
    old = {"id": 10, "owner_id": 3, "amount": 1}
    evidence = {
        "instruction": task["instruction"],
        "status": "success",
        "answer": '{"ids":[27],"amount":21.5}',
        "before": {"demo.Transfer": [old]},
        "after": {"demo.Transfer": [old, {"id": 27, "owner_id": 3, "amount": 21.5}]},
        "actions": [],
    }
    return task, evidence


def test_current_created_ids_not_teacher_ids_and_legal_alternative_method():
    task, e = fixture()
    assert objective(task, e)["success"]
    e["after"]["demo.Transfer"][1]["id"] = 99
    e["answer"] = '{"amount":21.5,"ids":[99]}'
    e["actions"] = [{"name": "demo__different_valid_payment_method"}]
    assert objective(task, e)["success"]


@pytest.mark.parametrize(
    "change",
    [
        "wrong_amount",
        "missing_ids",
        "extra_transfer",
        "foreign_owner",
        "unrelated_mutation",
        "deleted",
    ],
)
def test_real_objective_negatives(change):
    task, e = fixture()
    if change == "wrong_amount":
        e["answer"] = '{"ids":[27],"amount":20}'
    if change == "missing_ids":
        e["answer"] = '{"amount":21.5}'
    if change == "extra_transfer":
        e["after"]["demo.Transfer"].append({"id": 28, "owner_id": 3, "amount": 21.5})
    if change == "foreign_owner":
        e["after"]["demo.Transfer"][1]["owner_id"] = 4
    if change == "unrelated_mutation":
        e["after"]["demo.Transfer"][0] = {"id": 10, "owner_id": 3, "amount": 8}
    if change == "deleted":
        e["after"]["demo.Transfer"].pop(0)
    result = objective(task, e)
    assert result["reward_valid"] and result["success"] is False
    assert finish_semantic(result, {"conditions": [{"passed": True}]}) == result


def test_free_text_actual_student_evidence_and_different_wording():
    task, e = fixture()
    task["success_conditions"]["semantic"] = [
        {
            "id": "message",
            "requirement": "Politely explain the quoted total",
            "actions": ["mail__send"],
        }
    ]
    e["actions"] = [
        {
            "name": "mail__send",
            "args": {"body": "Thank you! Here is the agreed total."},
            "failed": False,
            "result": {"sent": True},
            "evidence_ref": {"rollout_id": "actual", "action_index": 7},
        }
    ]
    result = objective(task, e)
    assert result["status"] == "needs_semantic" and result["success"] is None
    seen = []

    def fake_judge(content):
        seen.append(content)
        return {
            "conditions": [
                {
                    "id": "message",
                    "passed": True,
                    "reason": "synthetic equivalent wording receipt",
                    "evidence_refs": [e["actions"][0]["evidence_ref"]],
                }
            ]
        }

    episode = {
        "evaluation": result,
        "success": None,
        "reward_valid": False,
        "classification": "infrastructure_error",
        "fault": None,
    }
    accepted = finalize(episode, fake_judge)
    assert accepted["success"] and accepted["reward_valid"]
    assert (
        seen[0]["conditions"][0]["evidence"][0]["args"]["body"]
        == e["actions"][0]["args"]["body"]
    )
    assert "teacher" not in str(seen)
    bad = finalize(
        episode,
        lambda _: {
            "conditions": [
                {
                    "id": "message",
                    "passed": True,
                    "reason": "claims done",
                    "evidence_refs": [],
                }
            ]
        },
    )
    assert bad["success"] is None and not bad["reward_valid"]


def test_whole_invalid_group_excluded_but_other_group_continues():
    base = {
        "task_id": "task",
        "generated_task_id": "new",
        "policy": "same",
        "initial_state_hash": "same",
        "classification": "natural_failure",
        "success": False,
        "reward_valid": True,
    }
    group = [copy.deepcopy(base) for _ in range(4)]
    group[0]["success"] = True
    assert any(group_advantages(group))
    group[1]["reward_valid"] = False
    assert group_advantages(group) is None
    assert not recovery_failure(group[1])
    assert recovery_failure(group[2])


def test_unsupported_condition_is_scoring_gap_not_bad_task():
    task, _ = fixture()
    task["success_conditions"]["checks"][0]["op"] = "eval_python"
    with pytest.raises(ScoringGap, match="Unsupported"):
        validate(task["success_conditions"])


def test_multifield_sets_and_order_follow_spec():
    task, e = fixture()
    task["success_conditions"]["checks"] = [
        check("IDs", {"answer": ["ids"]}, literal([1, 2]), "set_equal")
    ]
    e["answer"] = '{"amount":21.5,"ids":[2,1]}'
    assert objective(task, e)["success"]
    task["success_conditions"]["checks"][0]["op"] = "equal"
    assert not objective(task, e)["success"]


def test_process_count_attachments_and_date_scope_are_objective():
    task, e = fixture()
    rows = [
        {
            "name": "mail__send",
            "args": {"attachments": ["bill.pdf"]},
            "result": {},
            "failed": False,
        },
        {
            "name": "pay__approve",
            "args": {"id": 1, "date": "2023-05-12"},
            "result": {},
            "failed": False,
        },
    ]
    e["actions"] = rows
    sends = {"actions": {"name": "mail__send"}}
    approvals = {"actions": {"name": "pay__approve"}}
    task["success_conditions"]["checks"] = [
        check("single_send", {"count": sends}, literal(1)),
        check(
            "required_attachment",
            {"get": {"from": sends, "path": [0, "args", "attachments"]}},
            literal(["bill.pdf"]),
            "set_equal",
        ),
        check(
            "only_may",
            {"count": approvals},
            {
                "count": {
                    "filter": {
                        "from": approvals,
                        "where": {"args.date": {"gte": literal("2023-05-01")}},
                    }
                }
            },
        ),
    ]
    assert objective(task, e)["success"]
    rows[0]["args"]["attachments"] = []
    assert not objective(task, e)["success"]
    rows[0]["args"]["attachments"] = ["bill.pdf"]
    rows.append(copy.deepcopy(rows[0]))
    assert not objective(task, e)["success"]
    rows.pop()
    rows[1]["args"]["date"] = "2023-04-30"
    assert not objective(task, e)["success"]


def test_invalid_group_produces_no_forgetting_or_all_failed(tmp_path):
    from recoveragent.s2_schedule import GroupFeedback
    from recoveragent.coevolve_signals import SignalLedger
    from test_s2_experiment import cfg, make_group

    groups = make_group(tmp_path, "bad", [False] * 4)
    groups[2]["reward_valid"] = False
    ledger = GroupFeedback(cfg()["task_evolution"]["signals"])
    event = ledger.observe(0, groups)
    assert (
        not event["complete"] and event["signals"] == [] and not ledger.state["recent"]
    )
    signals = SignalLedger(window=2, minimum_observations=1, rare_percent=90)
    assert signals.observe(groups, group=True) == []
    assert not signals.recent and signals.pattern_total == 0


def test_public_goal_generation_repairs_once_then_defers(tmp_path, monkeypatch):
    from test_s2_evolution import engine
    from test_s2_experiment import cfg
    from test_coevolve import history

    e = engine(tmp_path)
    e.public_goals = True
    calls = []
    monkeypatch.setattr(
        e,
        "execute",
        lambda source, directory, payload: {
            "task_id": source,
            "trajectory_id": directory.name,
            "initial_state_hash": "same",
            "messages": history(),
        },
    )

    def text(source, directory, prompt, content):
        calls.append(directory.name)
        if directory.name == "abstraction":
            assert (
                "success_conditions" in prompt
                and "No fixed task family whitelist" in prompt
            )
            return {
                "tasks": [
                    {
                        "query": "A new public goal",
                        "action_sequence": "three actions",
                        "completion_criterion": "observable goal",
                        "evidence_refs": [
                            {k: row[k] for k in ("rollout_id", "action_index")}
                            for row in content["interaction_triplets"][:3]
                        ],
                    }
                ]
            }
        assert directory.name == "contract_repair"
        return {"success_conditions": {"revision": REVISION}}  # Still lacks output.

    monkeypatch.setattr(e, "text", text)
    e.generate(cfg()["feedback_task_ids"][0], {}, tmp_path / "context")
    assert calls == ["abstraction", "contract_repair"]
    assert not e.pool and len(e.attempts) == 1
    assert e.attempts[0]["quality_status"] == "deferred_scoring"


def test_scorer_failure_not_converted_to_zero():
    task, e = fixture()
    task["success_conditions"]["semantic"] = [
        {"id": "body", "requirement": "appropriate email", "actions": ["mail__send"]}
    ]
    original = {"evaluation": objective(task, e), "success": None, "fault": None}

    def unavailable(_):
        raise RuntimeError("API unavailable")

    result = finalize(original, unavailable)
    assert result["success"] is None and result["reward_valid"] is False
    assert result["classification"] == "scoring_gap"


def test_goal_capture_and_student_evaluate_use_same_conditions():
    from types import SimpleNamespace
    from recoveragent.coevolve_goal import prepare, evaluate
    from recoveragent.coevolve_tasks import capture_terminal

    task, e = fixture()

    class Row:
        def __init__(self, data):
            self.data = data

        def model_dump(self, mode):
            return self.data

    current = list(e["before"]["demo.Transfer"])
    table = SimpleNamespace(find_all=lambda: [Row(r) for r in current])
    adapter = SimpleNamespace(
        world=SimpleNamespace(
            models={"demo": {"Transfer": table}},
            requester=SimpleNamespace(
                request=lambda *a, **kw: {
                    "instruction": task["instruction"],
                    "status": "success",
                    "answer": e["answer"],
                }
            ),
        )
    )
    prepare(adapter, task)
    current[:] = e["after"]["demo.Transfer"]
    student = evaluate(adapter, task, [])
    trajectory = {"messages": []}
    capture_terminal(adapter, trajectory)
    teacher = trajectory["coevolve_terminal"]["goal_evaluation"]
    assert student == teacher and student["success"]
