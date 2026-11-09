"""One isolated AppWorld worker; model generation remains in the GPU parent."""

import contextlib
import copy
import json
import sys
from pathlib import Path

from .adapter import AppWorldAdapter
from .evaluation import environment_check, evaluate_isolated
from .execution_v6 import SYSTEM_PROMPT
from .harness import BUDGET_REVISION, REFACTOR_REVISION, ProtocolHarness
from .io import digest, read_json, write_json
from .p3c import observable_error, save_boundary
from .runner import now
from .schema_compatibility import SCHEMA_REVISION
from .student import StudentSession
from .v2_model import load_tokenizer
from .v2_tasks import RUNTIME, context_for


def run(config, generate):
    directory = Path(config["output"])
    directory.mkdir(parents=True, exist_ok=False)
    experiment = directory.name + "_" + digest(str(directory))[:12]
    task, adapter, session = config["task_id"], None, None
    if config["purpose"] not in {"feedback", "grpo", "evaluation"}:
        raise ValueError("Unknown episode purpose")
    training = config["purpose"] in {"feedback", "grpo"}
    context = context_for(task, training=training)
    checkpoints, fault, evaluation = [], None, None
    costs = {
        "model_seconds": 0.0,
        "environment_seconds": 0.0,
        "snapshot_seconds": 0.0,
        "evaluation_seconds": 0.0,
    }
    started = now()
    try:
        environment_check()
        adapter = AppWorldAdapter(
            config["appworld_root"],
            task,
            experiment,
            seed=42,
            max_interactions=sys.maxsize,
            research=context,
        )
        adapter.configure_schema_compatibility(SCHEMA_REVISION, directory)
        execute = adapter.execute

        def timed_execute(*args, **kwargs):
            before = now()
            try:
                return execute(*args, **kwargs)
            finally:
                costs["environment_seconds"] += now() - before

        adapter.execute = timed_execute
        generated = config.get("generated_task")
        if generated:
            from .v2_generator import public_task, validate_task, apply_instruction

            validate_task(generated, task)
            apply_instruction(adapter, generated["instruction"])
            public = public_task(adapter.public_task(), generated)
            if (
                adapter.fingerprint()
                != generated["validation"]["results"][0]["initial_state_hash"]
            ):
                raise ValueError(
                    "Generated initial state differs from validated source"
                )
        else:
            public = adapter.public_task()
        initial_hash = adapter.fingerprint()
        session = StudentSession(
            adapter,
            load_tokenizer(config["model"]),
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(public, ensure_ascii=False)},
            ],
            adapter.initial_tools(),
            tool_mode="dynamic_discovery",
            output=directory / "session",
            checkpoint_id=config["checkpoint_id"],
            clock=now,
            model_contract=config["model"],
            execution_protocol=RUNTIME,
            protocol_harness=True,
            answer_guard=True,
            harness_revision=REFACTOR_REVISION,
            **config["student_limits"],
        )
        session.runtime_revision = RUNTIME
        session.harness = ProtocolHarness(
            "dynamic_discovery",
            revision=REFACTOR_REVISION,
            budget_revision=BUDGET_REVISION,
        )

        def snapshot(kind):
            if (
                adapter.world.task_completed()
                or session.answer_guard.stopped
                or session.usage["unknown_generation_reservation"]
            ):
                return
            before = now()
            saved = save_boundary(
                session,
                directory / f"snapshot_{len(checkpoints):02d}",
                "pre_termination" if kind == "late_failure" else "post_action",
                {"kind": kind, "policy": session.identity},
            )
            checkpoints.append({**saved, "kind": kind})
            costs["snapshot_seconds"] += now() - before

        if config["purpose"] == "feedback":

            def before_dispatch(message):
                if any(
                    c["function"]["name"] == "supervisor__complete_task"
                    for c in message.get("tool_calls", [])
                ):
                    snapshot("late_failure")

            session.before_dispatch = before_dispatch
        while not session.stopped:
            try:

                def timed_generate(request):
                    response = generate(request)
                    costs["model_seconds"] += response.get("generation_seconds", 0.0)
                    return response

                event = session.step(timed_generate)
            except ValueError:
                if session.termination not in {
                    "budget_exhausted",
                    "context_budget_exhausted",
                    "completion_claim",
                }:
                    raise
                break
            if (
                config["purpose"] == "feedback"
                and observable_error(event)
                and not checkpoints
            ):
                snapshot("post_error")
        if config["purpose"] == "feedback" and not adapter.world.task_completed():
            snapshot("late_failure")
        write_json(
            directory / "history.json",
            {"messages": session.messages, "tools": session.tools},
        )
        if generated:
            from .v2_generator import evaluate

            evaluation = evaluate(adapter, generated)
        adapter.save_terminal()
        adapter.close()
        adapter = None
        if not generated:
            before = now()
            evaluation = evaluate_isolated(
                task,
                experiment,
                config["appworld_root"],
                directory,
                research=context,
            )
            costs["evaluation_seconds"] += now() - before
    except Exception as exc:
        fault = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if adapter:
            adapter.close()
    valid = (
        fault is None and evaluation is not None and evaluation.get("status") == "valid"
    )
    result = {
        "task_id": task,
        "experiment_name": experiment,
        "task_source": "generated" if config.get("generated_task") else "original",
        "generated_task_id": config.get("generated_task", {}).get("task_id"),
        "purpose": config["purpose"],
        "policy": session.identity if session else None,
        "initial_state_hash": initial_hash if session else None,
        "custom_research": context,
        "evaluation": evaluation,
        "success": evaluation["success"] if valid else None,
        "classification": "success"
        if valid and evaluation["success"]
        else "natural_failure"
        if valid
        else "infrastructure_error",
        "usage": copy.deepcopy(session.usage) if session else {},
        "costs": costs,
        "termination": session.termination if session else None,
        "fault": fault,
        "wall_seconds": now() - started,
        "snapshots": checkpoints,
        "selected_snapshot": checkpoints[-1]
        if checkpoints and valid and not evaluation["success"]
        else None,
    }
    write_json(directory / "result.json", result)
    return result


def main():
    channel = sys.stdout

    def generate(request):
        channel.write(json.dumps({"request": request}) + "\n")
        channel.flush()
        response = json.loads(sys.stdin.readline())
        if "error" in response:
            raise RuntimeError(response["error"])
        return response

    with contextlib.redirect_stdout(sys.stderr):
        result = run(read_json(sys.argv[1]), generate)
    channel.write(json.dumps({"result": result}) + "\n")
    channel.flush()


if __name__ == "__main__":
    main()
