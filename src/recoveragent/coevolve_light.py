"""Lightweight generation plugged into the existing S2 context/window controller."""

import json
from pathlib import Path

from .coevolve_llm import REVISION, JudgeError, admit
from .coevolve_prompts import GENERATION
from .coevolve_signals import interaction_triplets
from .coevolve_tasks import candidate
from .harness import public_tool_error
from .io import digest, read_json, write_json
from .s2_state import evidence
from .v2_tasks import context_for


def context(engine, event, directory):
    context_for(event["task_id"], training=True)
    if (
        not event.get("complete")
        or len(event["episodes"]) != 4
        or any(type(v) is not bool for v in event["outcomes"])
    ):
        raise ValueError("Light generation requires four valid same-group attempts")
    if (
        len({digest((r["policy"], r["initial_state_hash"])) for r in event["episodes"]})
        != 1
    ):
        raise ValueError("Feedback policy/initial state differs")
    records = []
    for row in event["episodes"]:
        messages = read_json(Path(row["directory"]) / "history.json")["messages"]
        if digest(messages) != row["history_sha256"]:
            raise ValueError("Feedback history changed")
        records.append(
            {
                "task_id": event["task_id"],
                "trajectory_id": row["directory"],
                "messages": messages,
                "success": row["success"],
                "initial_state_hash": row["initial_state_hash"],
            }
        )
    result = {"group": event, "trajectories": records}
    evidence(Path(directory) / "group_input.json", result)
    evidence(Path(directory) / "context_result.json", result)
    return result  # No separate analysis/quality model invocation.


def needs_exploration(records):
    # This only chooses whether to gather evidence, not whether a candidate can pass.
    return not any(
        not row["failed"]
        and not public_tool_error(row["observation"])
        and not row["action"]["name"].startswith(("api_docs__", "supervisor__"))
        for record in records
        for row in interaction_triplets(record["messages"], record["trajectory_id"])
    )


def public_instruction(messages):
    first = next(m for m in messages if m["role"] == "user")
    instruction = json.loads(first["content"])["instruction"]
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("Missing public task instruction")
    return instruction


def same_source_tasks(pool, source):
    return [
        {"task_id": t["task_id"], "query": t["instruction"]}
        for t in pool
        if t["source_task_id"] == source
    ]


def task_comparison(engine, source, feedback):
    event = feedback["group"]
    parent = public_instruction(feedback["trajectories"][0]["messages"])
    if any(
        public_instruction(r["messages"]) != parent for r in feedback["trajectories"]
    ):
        raise ValueError("Four attempts must share the same public task")
    root = parent
    if event["task_key"] != source:
        originals = [
            r
            for r in engine.summary["feedback"]
            if r["task_id"] == source and not r.get("generated_task_id")
        ]
        if len(originals) != 1:
            raise ValueError("Missing original public train instruction")
        root = public_instruction(
            read_json(Path(originals[0]["directory"]) / "history.json")["messages"]
        )
    return {
        "root_task": {"task_id": source, "query": root},
        "parent_task": {"task_id": event["task_key"], "query": parent},
        "same_source_tasks": same_source_tasks(engine.pool, source),
    }


def abstraction_input(engine, source, feedback, explored, limit):
    return {
        "max_tasks": limit,
        "feedback": feedback,
        "task_comparison": task_comparison(engine, source, feedback),
        "exploration": [
            {"trajectory_id": r["trajectory_id"], "messages": r["messages"]}
            for r in explored
        ],
    }


def duplicate_key(query):
    # Preserve case, numbers, negation, spacing and punctuation inside the task.
    # Only ignore an outer sentence period after a quoted phrase (the sweater
    # case), never a period inside the requested literal or a decimal number.
    text = query.strip()
    if len(text) > 2 and text[-1] in ".。":
        opening = {'"': '"', "'": "'", "”": "“", "’": "‘"}.get(text[-2])
        if opening and opening in text[:-2]:
            text = text[:-1]
    return text


def duplicate_of(query, comparison, pool, source):
    candidates = [
        ("root_task", comparison["root_task"]),
        ("parent_task", comparison["parent_task"]),
        *(("same_source_task", t) for t in same_source_tasks(pool, source)),
    ]
    key = duplicate_key(query)
    for kind, task in candidates:
        if duplicate_key(task["query"]) == key:
            return {"kind": kind, **task}
    return None


def generate(engine, source, feedback, directory):
    context_for(source, training=True)
    directory = Path(directory)
    records = feedback["trajectories"]
    limit = min(
        engine.settings["proposals_per_context"],
        engine.settings["max_proposals"] - len(engine.attempts),
    )
    if limit <= 0:
        return
    explored = []
    if needs_exploration(records):
        explored.append(
            engine.execute(
                source,
                directory / f"{directory.name}_explore_0",
                {
                    "mode": "exploration",
                    "context": feedback,
                    "scoring_revision": REVISION,
                },
            )
        )
    evidence(
        directory / "exploration_choice.json",
        {
            "count": len(explored),
            "maximum": 1,
            "reason": "missing_successful_business_observation"
            if explored
            else "feedback_contains_real_environment_evidence",
        },
    )
    content = abstraction_input(engine, source, feedback, explored, limit)
    proposed = engine.text(
        source,
        directory / "abstraction",
        GENERATION,
        content,
    )
    if (
        not isinstance(proposed, dict)
        or set(proposed) != {"tasks"}
        or not isinstance(proposed["tasks"], list)
        or len(proposed["tasks"]) > limit
    ):
        raise ValueError("Expected tasks array within the candidate budget")
    for proposal in proposed["tasks"]:
        target = directory / f"{directory.name}_proposal_{len(engine.attempts):02d}"
        write_json(target / "proposal.json", proposal)
        receipt = {
            "directory": str(target),
            "source_task_id": source,
            "accepted": False,
            "scoring_revision": REVISION,
        }
        try:
            task = candidate(
                proposal, records + explored, feedback, scoring_revision=REVISION
            )
            write_json(target / "candidate.json", task)
            duplicate = duplicate_of(
                task["instruction"], content["task_comparison"], engine.pool, source
            )
            if duplicate:
                receipt.update(
                    quality_status="duplicate_existing",
                    existing_task_id=duplicate["task_id"],
                    duplicate_of=duplicate,
                    reason="Existing task remains trainable; not counted as new",
                )
            else:
                accepted = admit(engine, task, target)
                engine.pool.append(accepted)
                receipt.update(accepted=True, task_id=accepted["task_id"])
        except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
            receipt.update(
                reason=str(exc),
                error_type=type(exc).__name__,
                quality_status="deferred_judge"
                if isinstance(exc, JudgeError)
                else "not_admitted",
            )
        engine.attempts.append(receipt)
        write_json(target / "acceptance.json", receipt)
