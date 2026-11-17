"""Lightweight generation plugged into the existing S2 context/window controller."""

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
    proposed = engine.text(
        source,
        directory / "abstraction",
        GENERATION,
        {
            "max_tasks": limit,
            "feedback": feedback,
            "exploration": [
                {"trajectory_id": r["trajectory_id"], "messages": r["messages"]}
                for r in explored
            ],
        },
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
            key = " ".join(task["instruction"].casefold().split())
            duplicate = next(
                (
                    t
                    for t in engine.pool
                    if t["source_task_id"] == source
                    and " ".join(t["instruction"].casefold().split()) == key
                ),
                None,
            )
            if duplicate:
                receipt.update(
                    quality_status="duplicate_existing",
                    existing_task_id=duplicate["task_id"],
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
