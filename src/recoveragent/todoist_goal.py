"""A bounded goal verifier for new Todoist project-and-task count proposals.

The public instruction and checks come from the same goal, not a reference DB
hash. Other generated task families keep their existing explicit contracts.
"""

import copy
import json
from datetime import date

from .coevolve_signals import interaction_triplets

REVISION = "todoist_project_tasks_v1"


def validate(goal):
    if (
        set(goal) != {"revision", "project_name", "tasks"}
        or goal["revision"] != REVISION
    ):
        raise ValueError("Unsupported Todoist goal contract")
    if not isinstance(goal["project_name"], str) or not goal["project_name"].strip():
        raise ValueError("A project name is required")
    if not isinstance(goal["tasks"], list) or not goal["tasks"]:
        raise ValueError("At least one task is required")
    for task in goal["tasks"]:
        required = {"title", "due_date", "priority"}
        if not required <= task.keys() or task.keys() - required - {"description"}:
            raise ValueError("Unsupported Todoist task fields")
        if not isinstance(task["title"], str) or not task["title"].strip():
            raise ValueError("A task title is required")
        if (
            not isinstance(task["due_date"], str)
            or date.fromisoformat(task["due_date"]).isoformat() != task["due_date"]
        ):
            raise ValueError("A YYYY-MM-DD due date is required")
        if task["priority"] not in {"low", "medium", "high"}:
            raise ValueError("Unsupported task priority")
        if "description" in task and not isinstance(task["description"], str):
            raise ValueError("Task description must be text")


def instruction(goal):
    validate(goal)
    return (
        "Create a new Todoist project named "
        + json.dumps(goal["project_name"], ensure_ascii=False)
        + " with exactly these tasks (due dates are calendar dates): "
        + json.dumps(goal["tasks"], ensure_ascii=False)
        + ". After creating them, list all tasks in that project to verify them, "
        "then report the number of tasks created."
    )


def rows(adapter, model):
    return [
        row.model_dump(mode="json")
        for row in adapter.world.models["todoist"][model].find_all()
    ]


def prepare(adapter, task):
    """Record the clean source's owner and existing project/task records."""
    goal = task.get("goal_contract")
    if goal is None:
        return
    if task["instruction"] != instruction(goal):
        raise ValueError("Todoist instruction differs from its goal contract")
    email = adapter.public_task()["supervisor"]["email"]
    owners = [r["id"] for r in rows(adapter, "User") if r["email"] == email]
    if len(owners) != 1:
        raise ValueError("Cannot identify the public supervisor's Todoist account")
    adapter.todoist_goal_start = {
        "owner": owners[0],
        "existing": {
            name: {r["id"]: r for r in rows(adapter, name)}
            for name in ("Project", "Task")
        },
    }


def matches(row, required):
    for key, value in required.items():
        actual = row.get(key)
        if key == "due_date" and isinstance(actual, str):
            actual = actual[:10]
        if actual != value:
            return False
    return True


def satisfied(adapter, task, messages):
    goal, start = task["goal_contract"], adapter.todoist_goal_start
    current = {
        name: {r["id"]: r for r in rows(adapter, name)} for name in ("Project", "Task")
    }
    # Creating this project must not delete or alter pre-existing Todoist work.
    if any(
        current[name].get(key) != row
        for name, old in start["existing"].items()
        for key, row in old.items()
    ):
        return False
    projects = [
        r
        for key, r in current["Project"].items()
        if key not in start["existing"]["Project"]
    ]
    if (
        len(projects) != 1
        or projects[0]["name"] != goal["project_name"]
        or projects[0]["user_id"] != start["owner"]
    ):
        return False
    project_id = projects[0]["id"]
    created = [
        r for key, r in current["Task"].items() if key not in start["existing"]["Task"]
    ]
    if len(created) != len(goal["tasks"]) or any(
        r["project_id"] != project_id
        or r["user_id"] != start["owner"]
        or r["is_completed"]
        for r in created
    ):
        return False
    # More specific descriptions match first when otherwise identical tasks exist.
    unmatched = list(created)
    for required in sorted(goal["tasks"], key=len, reverse=True):
        found = next(
            (i for i, row in enumerate(unmatched) if matches(row, required)), None
        )
        if found is None:
            return False
        unmatched.pop(found)
    seen = set()
    for row in interaction_triplets(messages, "goal_check"):
        name = row["action"]["name"]
        if row["failed"]:
            continue
        if name.startswith(
            (
                "todoist__create_",
                "todoist__update_",
                "todoist__delete_",
                "todoist__complete_",
            )
        ):
            seen.clear()
        if name == "todoist__show_tasks":
            args = json.loads(row["action"]["arguments"])
            observed = json.loads(row["observation"])
            if (
                args.get("project_id") == project_id
                and isinstance(observed, dict)
                and observed.get("project_id") == project_id
            ):
                listed = list(observed.get("no_section_tasks", []))
                for section in observed.get("sections", []):
                    listed.extend(section["tasks"])
                seen.update(
                    r["task_id"]
                    for r in listed
                    if isinstance(r, dict) and "task_id" in r
                )
    return {r["id"] for r in created} <= seen


def validation_modes(goal):
    modes = {
        "positive": True,
        "noop": False,
        "omit_completion": False,
        "wrong_answer": False,
        "omit_task": False,
        "wrong_due_date": False,
        "wrong_priority": False,
        "omit_readback": False,
    }
    if any("description" not in t for t in goal["tasks"]):
        modes["extra_description"] = True
    return modes


def perturb(name, args, mode, goal, changed):
    """Mutate one real reference call for a goal-specific execution check."""
    args = copy.deepcopy(args)
    if mode == "omit_readback" and name == "todoist__show_tasks":
        return None, True
    if name != "todoist__create_task" or changed:
        return args, changed
    if mode == "omit_task":
        return None, True
    if mode == "wrong_due_date":
        args["due_date"] = (
            "2000-01-01" if args.get("due_date") != "2000-01-01" else "2000-01-02"
        )
    elif mode == "wrong_priority":
        args["priority"] = "low" if args.get("priority") != "low" else "high"
    elif mode == "extra_description":
        if not any("description" not in t and matches(args, t) for t in goal["tasks"]):
            return args, changed
        args["description"] = "An optional note from the agent."
    else:
        return args, changed
    return args, True
