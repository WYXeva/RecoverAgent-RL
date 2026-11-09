"""One bounded generator: sum Gmail thread-category sizes under a read filter.

The strong model explores real public APIs and proposes the filter/categories.
Controller-side replay constructs the initial state and computes the oracle.
This is a new custom task evaluator, never an AppWorld official task score.
"""

import copy
import json
from pathlib import Path

from .io import digest, write_json, canonical
from .v2_tasks import context_for

VERSION = "gmail_category_count_v1"
CATEGORIES = {"inbox", "outbox", "snoozed", "starred", "archived", "spam"}


def exploration_instruction(feedback):
    return (
        "Explore Gmail using public APIs to design one new counting task addressing the Student weakness below. "
        "Do not change email state. Inspect read/unread thread-category counts. Choose a boolean read filter "
        "and exactly two different categories from inbox/outbox/snoozed/starred/archived/spam, both nonempty. "
        "Finish with supervisor.complete_task(answer=...) where answer is a JSON string containing only "
        "read (boolean), categories (two strings), and rationale (brief text linked to the feedback). "
        "Do not provide an expected answer. This is a task-generation exploration, not the source task's evaluation. "
        "Student feedback (public execution only): "
        + json.dumps(feedback, ensure_ascii=False)
    )


def apply_instruction(adapter, instruction):
    # Only the isolated derivative initial state changes, never installed task files.
    rows = adapter.world.models["supervisor"]["Task"].find_all()
    if len(rows) != 1 or rows[0].status is not None:
        raise ValueError("Generated task requires a clean active initial state")
    rows[0].instruction = instruction
    rows[0].save()
    adapter.world.task.instruction = instruction
    adapter.generated_business_sha256 = business_hash(adapter)


def business_hash(adapter):
    tables = {}
    for app, models in adapter.world.models.items():
        for name, model in models.items():
            if name == "SQLModel" or (app, name) == ("supervisor", "Task"):
                continue
            tables[f"{app}.{name}"] = sorted(
                [row.model_dump(mode="json") for row in model.find_all()], key=canonical
            )
    return digest(tables)


def public_task(original, task):
    return {**original, "instruction": task["instruction"]}


def parse_proposal(trajectory):
    context_for(trajectory["task_id"], training=True)
    calls, observations, proposal = [], [], None
    for i, message in enumerate(trajectory["messages"]):
        for j, call in enumerate(message.get("tool_calls", [])):
            fn = call["function"]
            name, args = fn["name"], json.loads(fn["arguments"])
            result = trajectory["messages"][i + j + 1]
            if result.get("tool_call_id") != call["id"]:
                raise ValueError("Exploration call/result mismatch")
            if name == "supervisor__complete_task":
                proposal = json.loads(args["answer"])
                continue
            if not (
                name.startswith("api_docs__")
                or name
                in {
                    "supervisor__show_profile",
                    "supervisor__show_active_task",
                    "supervisor__show_account_passwords",
                    "gmail__login",
                    "gmail__show_category_sizes",
                }
            ):
                raise ValueError("Unsupported or mutating exploration action")
            if "Execution failed" in result["content"]:
                raise ValueError("Exploration solution contains a failed action")
            calls.append({"function": fn, "result": result["content"]})
            if name == "gmail__show_category_sizes":
                observations.append((args.get("read"), json.loads(result["content"])))
    if (
        not isinstance(proposal, dict)
        or set(proposal) != {"read", "categories", "rationale"}
        or type(proposal["read"]) is not bool
        or not isinstance(proposal["rationale"], str)
    ):
        raise ValueError("Malformed model task proposal")
    categories = proposal["categories"]
    if (
        not isinstance(categories, list)
        or len(categories) != 2
        or len(set(categories)) != 2
        or not set(categories) <= CATEGORIES
    ):
        raise ValueError("Unsupported category proposal")
    counts = next(
        (values for read, values in observations if read is proposal["read"]), None
    )
    if counts is None or any(
        type(counts.get(c)) is not int or counts[c] <= 0 for c in categories
    ):
        raise ValueError("Proposal lacks actual nonempty public observations")
    return proposal, calls, sum(counts[c] for c in categories)


def candidate(trajectory, feedback, exploration_ref):
    proposal, calls, expected = parse_proposal(trajectory)
    condition = "read" if proposal["read"] else "unread"
    categories = sorted(proposal["categories"])
    instruction = (
        f"In my Gmail account, what is the sum of the numbers of {condition} email threads "
        f"in these categories: {', '.join(categories)}? Count a thread once in each selected category "
        "where it appears, then add the two category counts. Return one integer. Do not change emails."
    )
    original_instruction = json.loads(trajectory["messages"][1]["content"])[
        "instruction"
    ]
    if instruction == original_instruction:
        raise ValueError("Generated goal did not change")
    task = {
        "generator": VERSION,
        "source_task_id": trajectory["task_id"],
        "source_context": context_for(trajectory["task_id"], training=True),
        "instruction": instruction,
        "read": proposal["read"],
        "categories": categories,
        "rationale": proposal["rationale"],
        "feedback_sha256": digest(feedback),
        "exploration_ref": str(exploration_ref),
        "exploration_sha256": digest(trajectory),
        "solution": calls,
        "expected_answer": expected,
        "evaluator": VERSION,
        "official_benchmark_claim": False,
    }
    return {**task, "task_id": "generated_" + digest(task)[:20]}


def validate_task(task, source_task_id, *, require_validation=True):
    context = context_for(source_task_id, training=True)
    if (
        task["generator"] != VERSION
        or task["source_task_id"] != source_task_id
        or task["source_context"] != context
        or task["evaluator"] != VERSION
    ):
        raise ValueError("Generated task source/evaluator mismatch")
    base = {k: v for k, v in task.items() if k not in {"task_id", "validation"}}
    if task["task_id"] != "generated_" + digest(base)[:20]:
        raise ValueError("Generated goal/oracle/solution changed")
    if require_validation and not task.get("validation", {}).get("passed"):
        raise ValueError("Unvalidated generated task")


def evaluate(adapter, task):
    current = adapter.world.requester.request(
        "supervisor", "show_active_task", track=False
    )
    success = (
        current.get("instruction") == task["instruction"]
        and current.get("status") == "success"
        and type(current.get("answer")) is int
        and current["answer"] == task["expected_answer"]
        and business_hash(adapter) == adapter.generated_business_sha256
    )
    return {
        "status": "valid",
        "success": success,
        "evaluator": VERSION,
        "reference": "generated_initial_state",
        "official_result_complete": False,
    }


def replay(adapter, task, mode, output):
    from .tool_modes import execute_with_mode
    from .io import files_manifest

    validate_task(task, adapter.task_id, require_validation=False)
    apply_instruction(adapter, task["instruction"])
    initial = adapter.fingerprint()
    adapter.export_state("generated_initial", Path(output) / "initial_dbs")
    if evaluate(adapter, task)["success"]:
        raise ValueError("No-op unexpectedly passes")
    tools, tokens, observed = adapter.initial_tools(), {}, None
    if mode != "noop":
        for entry in task["solution"]:
            fn = entry["function"]
            args = json.loads(fn["arguments"])
            if "access_token" in args:
                args["access_token"] = tokens.get(
                    args["access_token"], args["access_token"]
                )
            result, tools = execute_with_mode(
                adapter, fn["name"], json.dumps(args), tools, "dynamic_discovery"
            )
            if "Execution failed" in result:
                raise ValueError("Generated solution replay failed")
            if fn["name"] == "gmail__login":
                old, new = json.loads(entry["result"]), json.loads(result)
                tokens[old["access_token"]] = new["access_token"]
            if (
                fn["name"] == "gmail__show_category_sizes"
                and args.get("read") is task["read"]
            ):
                counts = json.loads(result)
                observed = sum(counts[c] for c in task["categories"])
        if observed != task["expected_answer"]:
            raise ValueError("Initial public observation/oracle not reproducible")
        if mode in {"positive", "wrong_answer"}:
            answer = observed + int(mode == "wrong_answer")
            adapter.execute(
                "supervisor__complete_task", json.dumps({"answer": answer}), tools
            )
    result = {
        "mode": mode,
        "initial_state_hash": initial,
        "evaluation": evaluate(adapter, task),
        "initial_files": files_manifest(Path(output) / "initial_dbs"),
    }
    write_json(Path(output) / "validation.json", result)
    return result


def accept(candidate, results):
    if {r["mode"] for r in results} != {
        "positive",
        "noop",
        "omit_completion",
        "wrong_answer",
    } or len(results) != 4:
        raise ValueError("Missing generator positive/negative replay")
    if len({r["initial_state_hash"] for r in results}) != 1 or any(
        r["evaluation"]["success"] != (r["mode"] == "positive") for r in results
    ):
        raise ValueError("Generated-task evaluator or initial-state checks failed")
    return {
        **copy.deepcopy(candidate),
        "validation": {"passed": True, "results": results, "sha256": digest(results)},
    }
