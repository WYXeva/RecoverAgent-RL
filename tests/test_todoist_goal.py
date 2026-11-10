"""Goal scoring regression tests: synthetic models and calls, no paid execution."""

import copy
import json
from types import SimpleNamespace

import pytest

from recoveragent import coevolve_tasks as tasks
from recoveragent import todoist_goal as goal
from recoveragent.coevolve_generation import Evolution
from recoveragent.io import read_json, write_json
from recoveragent.v2_round import config
from recoveragent.v2_tasks import context_for, plan


def specification(description=False):
    value = {
        "revision": goal.REVISION,
        "project_name": "Movie Night",
        "tasks": [
            {"title": "Buy snacks", "due_date": "2023-05-22", "priority": "high"},
            {"title": "Reserve tickets", "due_date": "2023-05-24", "priority": "high"},
            {"title": "Collect shares", "due_date": "2023-05-20", "priority": "medium"},
        ],
    }
    if description:
        value["tasks"][0]["description"] = "Bring enough for everyone"
    return value


class Row:
    def __init__(self, value):
        self.value = value

    def model_dump(self, mode):
        return copy.deepcopy(self.value)


class Adapter:
    def __init__(self):
        self.task_id = plan()["feedback20"][0]
        self.data = {
            "User": [{"id": 8, "email": "owner@example.test"}],
            "Project": [],
            "Task": [],
        }
        self.current = {"status": None, "answer": None}
        self.world = SimpleNamespace(
            models={
                "todoist": {
                    name: SimpleNamespace(
                        find_all=lambda name=name: [Row(r) for r in self.data[name]]
                    )
                    for name in self.data
                }
            },
            requester=SimpleNamespace(request=lambda *a, **k: self.current),
        )

    def public_task(self):
        return {"supervisor": {"email": "owner@example.test"}}

    def initial_tools(self):
        return []

    def fingerprint(self):
        return "initial"


def call(messages, name, args, result):
    cid = str(len(messages))
    messages.extend(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": cid,
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": cid, "content": json.dumps(result)},
        ]
    )


def task_for(spec):
    source = plan()["feedback20"][0]
    return {
        "generator": tasks.VERSION,
        "source_task_id": source,
        "source_context": context_for(source, training=True),
        "goal_contract": copy.deepcopy(spec),
        "instruction": goal.instruction(spec),
    }


def listing(adapter):
    # Pinned public show_tasks response: section groups and task_id, not raw rows.
    return {
        "project_id": 20,
        "no_section_tasks": [
            {"task_id": r["id"], **{k: v for k, v in r.items() if k != "id"}}
            for r in adapter.data["Task"]
        ],
        "sections": [],
    }


def completed(spec=None):
    spec = spec or specification()
    adapter, messages, task = Adapter(), [], task_for(spec)
    adapter.current["instruction"] = task["instruction"]
    goal.prepare(adapter, task)
    adapter.data["Project"].append(
        {"id": 20, "user_id": 8, "name": spec["project_name"]}
    )
    call(
        messages, "todoist__create_project", {"name": spec["project_name"]}, {"id": 20}
    )
    for i, requested in enumerate(spec["tasks"]):
        record = {
            "id": 30 + i,
            "user_id": 8,
            "project_id": 20,
            "is_completed": False,
            "description": "",
            **requested,
        }
        adapter.data["Task"].append(record)
        call(messages, "todoist__create_task", {"project_id": 20, **requested}, record)
    call(messages, "todoist__show_tasks", {"project_id": 20}, listing(adapter))
    adapter.current.update(status="success", answer=len(spec["tasks"]))
    call(messages, "supervisor__complete_task", {"answer": len(spec["tasks"])}, {})
    return adapter, messages, task


def referenced(adapter, messages, task):
    reference = {
        "status": "valid",
        "initial_state_hash": "initial",
        "trajectory_id": "solver",
        "messages": messages,
        "coevolve_terminal": {
            **adapter.current,
            "business_sha256": "reference",
            "goal_satisfied": goal.satisfied(adapter, task, messages),
        },
    }
    return tasks.attach_reference(
        task, reference, {"success": True, "reason": "synthetic"}
    ), reference


def test_actual_regression_optional_description_does_not_change_reward():
    adapter, messages, task = completed()
    task, _ = referenced(adapter, messages, task)
    assert tasks.evaluate(adapter, task, messages)["success"]
    for row in adapter.data["Task"]:
        row["description"] = row["title"]
    assert tasks.evaluate(adapter, task, messages)["success"]
    assert tasks.evaluate(adapter, task, messages)["evaluator"] == goal.REVISION


@pytest.mark.parametrize(
    "mutation",
    [
        lambda a: a.data["Task"].pop(),
        lambda a: a.data["Task"].append({**a.data["Task"][0], "id": 99}),
        lambda a: a.data["Task"][0].update(due_date="2023-05-23"),
        lambda a: a.data["Task"][0].update(priority="low"),
        lambda a: a.data["Task"][0].update(title="Wrong task"),
        lambda a: a.data["Task"][0].update(project_id=999),
        lambda a: a.data["Task"][0].update(user_id=999),
        lambda a: a.data["Task"][0].update(is_completed=True),
        lambda a: a.data["Project"][0].update(name="Wrong project"),
        lambda a: a.data["Project"][0].update(user_id=999),
    ],
)
def test_wrong_business_conditions_still_fail(mutation):
    adapter, messages, task = completed()
    assert goal.satisfied(adapter, task, messages)
    mutation(adapter)
    assert not goal.satisfied(adapter, task, messages)


def test_explicit_description_remains_required():
    adapter, messages, task = completed(specification(description=True))
    assert goal.satisfied(adapter, task, messages)
    adapter.data["Task"][0]["description"] = "Something else"
    assert not goal.satisfied(adapter, task, messages)


def test_readback_is_required_after_creation_and_bound_to_project():
    adapter, messages, task = completed()
    assert not goal.satisfied(adapter, task, messages[:-4])
    wrong = copy.deepcopy(messages)
    wrong[-4]["tool_calls"][0]["function"]["arguments"] = '{"project_id":999}'
    assert not goal.satisfied(adapter, task, wrong)
    call(messages, "todoist__update_task", {"task_id": 30, "title": "Buy snacks"}, {})
    assert not goal.satisfied(adapter, task, messages)


@pytest.mark.parametrize("answer", [None, True, 2, 4, "three"])
def test_missing_or_wrong_count_fails(answer):
    adapter, messages, task = completed()
    task, _ = referenced(adapter, messages, task)
    adapter.current["answer"] = answer
    assert not tasks.evaluate(adapter, task, messages)["success"]


def test_existing_work_cannot_be_modified():
    adapter, messages, task = completed()
    old = {"id": 1, "user_id": 8, "name": "Existing"}
    adapter.todoist_goal_start["existing"]["Project"][1] = old
    adapter.data["Project"].append(copy.deepcopy(old))
    assert goal.satisfied(adapter, task, messages)
    adapter.data["Project"][-1]["name"] = "Deleted my old name"
    assert not goal.satisfied(adapter, task, messages)


def test_legacy_reward_and_validation_modes_are_unchanged(monkeypatch):
    adapter, messages, new = completed()
    legacy = {
        "instruction": new["instruction"],
        "execution_contract": {
            "revision": tasks.VERSION,
            "expected_answer": 3,
            "expected_business_sha256": "reference",
            "answer_comparison": {"kind": "scalar_number"},
        },
    }
    monkeypatch.setattr(tasks, "business_hash", lambda _: "different")
    assert not tasks.evaluate(adapter, legacy)["success"]
    assert len(tasks.validation_modes(legacy)) == 4
    assert len(tasks.validation_modes(new)) == 9


def test_goal_checks_execute_realistic_mutations_in_shared_replay(
    tmp_path, monkeypatch
):
    adapter, messages, task = completed()
    task, reference = referenced(adapter, messages, task)
    monkeypatch.setattr(
        tasks,
        "apply_instruction",
        lambda a, instruction: a.current.update(instruction=instruction),
    )

    def execute(a, name, raw, tools, mode):
        args = json.loads(raw)
        result = {}
        if name == "todoist__create_project":
            result = {"id": 20, "user_id": 8, **args}
            a.data["Project"].append(result)
        elif name == "todoist__create_task":
            result = {
                "id": 30 + len(a.data["Task"]),
                "user_id": 8,
                "is_completed": False,
                "description": "",
                **args,
            }
            a.data["Task"].append(result)
        elif name == "todoist__show_tasks":
            result = listing(a)
        elif name == "supervisor__complete_task":
            a.current.update(status="success", **args)
        return json.dumps(result), tools

    monkeypatch.setattr("recoveragent.tool_modes.execute_with_mode", execute)
    results = []
    for mode, expected in tasks.validation_modes(task).items():
        outcome = tasks.replay(Adapter(), task, reference, mode, tmp_path / mode)
        assert outcome["evaluation"]["success"] is expected, mode
        results.append(outcome)
    accepted = tasks.accept(task, results)
    tasks.validate_task(accepted, task["source_task_id"])
    missing_business_checks = results[:4]
    with pytest.raises(ValueError, match="positive/negative"):
        tasks.accept(task, missing_business_checks)


def test_reference_and_public_goal_cannot_disagree():
    adapter, messages, task = completed()
    task, reference = referenced(adapter, messages, task)
    reference["coevolve_terminal"]["goal_satisfied"] = False
    with pytest.raises(ValueError, match="public Todoist"):
        tasks.attach_reference(task, reference, {"success": True, "reason": "claim"})
    task["instruction"] += " Also do an unrelated operation."
    task["task_id"] = tasks.identify(task)
    with pytest.raises(ValueError, match="instruction"):
        tasks.validate_task(task, task["source_task_id"], require_validation=False)


def test_sectioned_readback_and_non_reference_ids_are_allowed():
    adapter, messages, task = completed()
    observed = listing(adapter)
    observed["sections"] = [
        {"section_id": 7, "tasks": observed.pop("no_section_tasks")}
    ]
    observed["no_section_tasks"] = []
    messages[-3]["content"] = json.dumps(observed)
    assert goal.satisfied(adapter, task, messages)
    # The score is tied to the goal, not the reference's primary keys/order.
    adapter.data["Project"][0]["id"] = 70
    for row in adapter.data["Task"]:
        row["project_id"] = 70
        row["id"] += 100
    adapter.data["Task"].reverse()
    observed = {
        "project_id": 70,
        "no_section_tasks": [{"task_id": row["id"]} for row in adapter.data["Task"]],
        "sections": [],
    }
    call(messages, "todoist__show_tasks", {"project_id": 70}, observed)
    assert goal.satisfied(adapter, task, messages)


def test_generation_carries_goal_to_solver_and_goal_specific_checks(
    tmp_path, monkeypatch
):
    adapter, messages, sample = completed()
    source = adapter.task_id
    feedback = tmp_path / "feedback"
    write_json(
        feedback / "history.json",
        {
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps({"instruction": "Retrieve a document."}),
                }
            ]
        },
    )
    summary = {
        "teacher_usage_priced_cny": 0,
        "feedback": [{"task_id": source, "directory": str(feedback)}],
    }
    engine = Evolution(config(), {}, {}, tmp_path, summary, 1e20, None)
    seen = []

    def execute(source_id, directory, payload):
        assert source_id == source
        if payload["mode"] == "exploration":
            return {
                "task_id": source,
                "trajectory_id": directory.name,
                "messages": messages,
            }
        task = payload["task"]
        seen.append(task)
        assert task["instruction"] == goal.instruction(specification())
        assert task["goal_contract"] == specification()
        return {
            "status": "valid",
            "initial_state_hash": "initial",
            "trajectory_id": "solver",
            "messages": messages,
            "coevolve_terminal": {
                "instruction": task["instruction"],
                "status": "success",
                "answer": 3,
                "business_sha256": "audit_only",
                "goal_satisfied": True,
            },
        }

    def text(source_id, directory, system, content):
        if directory.name == "abstraction":
            assert "goal_contract" in system
            return {
                "tasks": [
                    {
                        "query": "Proposal wording retained only in its raw audit.",
                        "action_sequence": "Create and verify the project and tasks.",
                        "completion_criterion": "The specified project and tasks exist and were read back.",
                        "goal_contract": specification(),
                        "evidence_refs": [
                            {key: row[key] for key in ("rollout_id", "action_index")}
                            for row in content["interaction_triplets"][:4]
                        ],
                    }
                ]
            }
        if directory.name == "novelty":
            assert content["candidate"] == goal.instruction(specification())
            return {
                "novel": True,
                "reason": "Different goal",
                "goal_difference": "Create tasks instead of retrieving a document",
                "duplicate_of": None,
            }
        return {"success": True, "reason": "Synthetic execution evidence"}

    def isolated(python, command, payload, directory, deadline):
        assert command == "generator-check"
        assert payload["task"]["execution_contract"]["revision"] == goal.REVISION
        write_json(
            directory / "validation.json",
            {
                "mode": payload["mode"],
                "initial_state_hash": "initial",
                "evaluation": {
                    "success": tasks.validation_modes(payload["task"])[payload["mode"]]
                },
            },
        )

    engine.deployment = {"worker_python": "synthetic", "appworld_root": "synthetic"}
    engine.isolated = isolated
    monkeypatch.setattr(engine, "execute", execute)
    monkeypatch.setattr(engine, "text", text)
    engine.generate(source, None, tmp_path / "generation")
    assert len(seen) == len(engine.pool) == 1
    task = engine.pool[0]
    assert len(task["validation"]["results"]) == 9
    assert engine.task_at(0, {})["generated_task"] == task
    tasks.validate_task(task, source)
    proposal = read_json(tmp_path / "generation/generation_proposal_00/proposal.json")
    assert proposal["query"] != task["instruction"]  # Original proposal stays in audit.
