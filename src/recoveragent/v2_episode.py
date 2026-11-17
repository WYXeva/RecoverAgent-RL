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
    if config["purpose"] not in {"feedback", "grpo", "evaluation", "protocol_probe"}:
        raise ValueError("Unknown episode purpose")
    training = config["purpose"] in {"feedback", "grpo", "protocol_probe"}
    probe = config["purpose"] == "protocol_probe"
    if (
        probe
        and config.get("completion_contract_revision") != "completion_submission_v2"
    ):
        raise ValueError("Protocol probe requires the new submission contract")
    final_test = config.get("final_test", False)
    if final_test:
        from .v2_runtime import S2_RUNTIME

        if (
            training
            or config.get("execution_protocol") != S2_RUNTIME
            or config.get("generated_task")
        ):
            raise ValueError(
                "Frozen test tasks are available only to S2 final evaluation"
            )
    context = context_for(task, training=training, final_test=final_test)
    checkpoints, fault, evaluation = [], None, None
    environment = None
    accepted_submission = None
    costs = {
        "model_seconds": 0.0,
        "environment_seconds": 0.0,
        "snapshot_seconds": 0.0,
        "evaluation_seconds": 0.0,
    }
    started = now()
    try:
        environment_check()
        from freezegun import configure

        configure(extend_ignore_list=["transformers", "torch", "huggingface_hub"])
        if config.get("environment_contract"):
            from .appworld_environment import verify_environment

            environment = verify_environment(config["environment_contract"])
            write_json(directory / "environment.json", environment)
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
                response = execute(*args, **kwargs)
                if environment is not None:
                    from .appworld_environment import require_usable_execution

                    require_usable_execution(response[0], args[0], directory)
                return response
            finally:
                costs["environment_seconds"] += now() - before

        adapter.execute = timed_execute
        generated = config.get("generated_task")
        if generated:
            from .v2_generator import public_task, apply_instruction
            from . import v2_generator as generated_backend

            if generated.get("generator") == "coevolve_appworld_task_v2":
                from . import coevolve_tasks as generated_backend

            if probe:
                # A frozen diagnostic definition is not admitted training data.
                # It has its own identity; no old validation/hash is edited to pass.
                generated_backend.validate_task(
                    generated, task, require_validation=False
                )
            else:
                generated_backend.validate_task(generated, task)
            from .coevolve_llm import is_task, REVISION as LLM_REVISION

            llm_task = is_task(generated)
            if (
                config.get("generated_scoring_revision") == LLM_REVISION
                and not llm_task
            ):
                raise ValueError(
                    "Generated pool has not been admitted under the LLM reward protocol"
                )
            if llm_task and config.get("generated_scoring_revision") != LLM_REVISION:
                raise ValueError(
                    "LLM task requires its explicit execution scoring identity"
                )
            apply_instruction(adapter, generated["instruction"])
            if "goal_contract" in generated:
                from .todoist_goal import prepare

                prepare(adapter, generated)
            if "success_conditions" in generated:
                from .coevolve_goal import prepare as prepare_goal

                prepare_goal(adapter, generated)
            elif config.get("completion_contract_revision") and not llm_task:
                from .coevolve_goal import ScoringGap

                raise ScoringGap(
                    "Generated task needs revalidation with public success conditions"
                )
            public = public_task(adapter.public_task(), generated)
            if not probe and (
                adapter.fingerprint()
                != (
                    generated["validation"]["initial_state_hash"]
                    if llm_task
                    else generated["validation"]["results"][0]["initial_state_hash"]
                )
            ):
                raise ValueError(
                    "Generated initial state differs from validated source"
                )
        else:
            public = adapter.public_task()
        from .teacher_answer_format import public_task as format_public_task

        public = format_public_task(public, config)
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
            execution_protocol=config.get("execution_protocol", RUNTIME),
            protocol_harness=True,
            answer_guard=True,
            harness_revision=REFACTOR_REVISION,
            **config["student_limits"],
        )
        session.runtime_revision = config.get("execution_protocol", RUNTIME)
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
        if config.get("completion_contract_revision"):
            current = adapter.world.requester.request(
                "supervisor", "show_active_task", track=False
            )
            if current.get("status") in {"success", "fail"}:
                accepted_submission = {
                    "status": current["status"],
                    "answer": current.get("answer"),
                }
        if generated:
            if "success_conditions" in generated:
                from .coevolve_goal import evidence as goal_evidence

                write_json(
                    directory / "goal_evidence.json",
                    goal_evidence(adapter, generated, session.messages),
                )
            if (
                llm_task
                or "goal_contract" in generated
                or "success_conditions" in generated
            ):
                evaluation = generated_backend.evaluate(
                    adapter,
                    generated,
                    session.messages,
                    **({"initial_state_hash": initial_hash} if llm_task else {}),
                )
            else:
                evaluation = generated_backend.evaluate(adapter, generated)
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
        from .coevolve_goal import ScoringGap
        from .coevolve_llm import JudgeError

        if isinstance(exc, (ScoringGap, JudgeError)):
            fault["origin"] = "scorer"
            evaluation = {
                "status": "scoring_gap",
                "success": None,
                "reward_valid": False,
                "reason": str(exc),
            }
        if environment is not None:
            if session and not (directory / "history.json").exists():
                write_json(
                    directory / "history.json",
                    {"messages": session.messages, "tools": session.tools},
                )
            if adapter:
                try:
                    adapter.save_terminal()
                except Exception as save_exc:
                    fault["terminal_save_error_type"] = type(save_exc).__name__
    finally:
        if adapter:
            try:
                adapter.close()
            except Exception as exc:
                cleanup = {"type": type(exc).__name__, "message": str(exc)}
                if fault is None:
                    fault = {"phase": "cleanup", **cleanup}
                else:
                    fault["cleanup"] = cleanup
    valid = (
        fault is None
        and not (session and session.reward_fault)
        and evaluation is not None
        and evaluation.get("status") == "valid"
        and evaluation.get("reward_valid", True)
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
        "reward_valid": bool(valid),
        "accepted_submission": accepted_submission,
        "failure_origin": "parser_harness"
        if session and session.reward_fault
        else "scorer"
        if evaluation
        and evaluation.get("status")
        in {"needs_semantic", "needs_llm_judge", "scoring_gap"}
        else "environment"
        if fault
        else "model"
        if valid and not evaluation["success"]
        else None,
        "classification": "success"
        if valid and evaluation["success"]
        else "natural_failure"
        if valid
        else "scoring_gap"
        if evaluation and evaluation.get("status") == "scoring_gap"
        else "infrastructure_error",
        "usage": copy.deepcopy(session.usage) if session else {},
        "costs": costs,
        "termination": session.termination if session else None,
        "fault": fault or (session.reward_fault if session else None),
        "environment": environment,
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
