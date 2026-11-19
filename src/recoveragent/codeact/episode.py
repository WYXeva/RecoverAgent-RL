"""Shared native-tool agent loop. Official scoring stays outside model history."""

import copy
import importlib.metadata
import json
from pathlib import Path
import hashlib
import os

from jsonschema import ValidationError

from recoveragent.appworld_environment import (
    REVISION as ENV_REVISION,
    execution_environment_error,
    verify_environment,
)
from recoveragent.evaluation import evaluate_isolated
from recoveragent.io import digest, write_json
from recoveragent.teacher import TeacherTransportError, normalize_message
from recoveragent.v2_tasks import authorize_task, context_for

from .protocol import TOOLS, BudgetExhausted, codes, identity, now, public_messages


def loop(world, client, config, directory):
    """Teacher and Student differ only in the client, not tools or environment."""
    messages = public_messages(world)
    write_json(directory / "initial.json", {"messages": messages, "tools": TOOLS})
    observations, corrections, fault = [], 0, None
    started = now()
    reason = "request_limit"
    for index in range(config["limits"]["max_requests"]):
        remaining = (
            config["limits"]["max_generated_tokens"] - client.usage["generated_tokens"]
        )
        if remaining <= 0 or now() - started >= config["limits"]["wall_seconds"]:
            reason = "token_limit" if remaining <= 0 else "wall_limit"
            break
        try:
            response = client.generate(
                messages, directory / "requests", index, remaining
            )
        except BudgetExhausted:
            reason = "client_budget"
            break
        except TeacherTransportError as exc:
            reason, fault = "teacher_service_error", str(exc)
            break
        except ValueError as exc:
            reason, fault = "client_limit_or_contract", str(exc)
            break
        try:
            message = normalize_message(response, max_calls=1, allow_reasoning=True)
            call, code = codes(message, messages)
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            write_json(
                directory / "rejections" / f"{index:03d}.json",
                {"error": str(exc), "response": response},
            )
            corrections += 1
            if corrections > config["limits"]["format_corrections"]:
                reason = "format_corrections_exhausted"
                break
            # Invalid native messages cannot be sent back as valid tool history.
            # Preserve the original in raw receipts, exclude this episode from SFT.
            messages.append(
                {
                    "role": "user",
                    "content": f"Your last reply was not executed: {exc}. Call appworld with one code string using the native tool interface.",
                }
            )
            continue
        client.remember(len(messages), response)
        messages.append(message)
        write_json(
            directory / "actions" / f"{index:03d}.intent.json",
            {"code": code, "call": call},
        )
        tick = now()
        observation = world.execute(code)
        event = {
            "request_index": index,
            "code": code,
            "observation": observation,
            "execution_error": observation.startswith("Execution failed."),
            "seconds": now() - tick,
        }
        write_json(directory / "actions" / f"{index:03d}.result.json", event)
        observations.append(event)
        messages.append(
            {"role": "tool", "tool_call_id": call["id"], "content": observation}
        )
        from .collect import replace_json

        replace_json(
            directory / "live.json",
            {
                "messages": messages,
                "actions": observations,
                "usage": copy.deepcopy(client.usage),
                "agent_seconds": now() - started,
            },
        )
        fault = execution_environment_error(observation)
        if fault:
            reason = "environment_error"
            break
        if world.task_completed():
            reason = "task_completed"
            break
    return {
        "messages": messages,
        "actions": observations,
        "termination": reason,
        "fault": fault,
        "format_rejections": corrections,
        "usage": copy.deepcopy(client.usage),
        "agent_seconds": now() - started,
    }


def run(
    task_id,
    attempt,
    directory,
    root,
    config,
    client,
    *,
    training=True,
    generated_task=None,
    instruction=None,
):
    from freezegun import configure

    # AppWorld freezes business time. Its scan must not resolve Transformers'
    # lazy model imports in the tokenizer-only vLLM client process.
    configure(extend_ignore_list=["transformers", "torch", "huggingface_hub"])
    from appworld import AppWorld, update_root

    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "worker.json", {"pid": os.getpid()})
    (directory / "requests").mkdir()
    context = context_for(task_id, training=training)
    if (generated_task is not None or instruction is not None) and not training:
        raise ValueError("Generated tasks and exploration must remain train-only")
    if generated_task is not None:
        from recoveragent.coevolve_llm import validate_task

        validate_task(generated_task, require_validation=False)
        if (
            generated_task["source_task_id"] != task_id
            or generated_task.get("execution_protocol") != identity()
        ):
            raise ValueError("Generated CodeAct task/source identity differs")
        instruction = generated_task["instruction"]
    environment = verify_environment(ENV_REVISION)
    package = json.loads(
        importlib.metadata.distribution("appworld").read_text("direct_url.json")
    )
    if config["appworld_revision"] not in json.dumps(package):
        raise ValueError("AppWorld source revision differs")
    update_root(str(Path(root).resolve()))
    authorize_task(root, task_id, context)
    experiment = "codeact_" + digest(str(directory))[:24]
    binding = {
        "task_id": task_id,
        "scenario_id": context["scenario_id"],
        "split": context["role"],
        "official_split": context["official_split"],
        "attempt": attempt,
        "protocol": identity(),
        "config_sha256": digest(config),
        "config": config,
        "environment": environment,
        "experiment": experiment,
        "custom_research": context,
        "model": getattr(client, "model_identity", config["teacher"]),
        "source_files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    write_json(directory / "binding.json", binding)
    started = now()
    world = AppWorld(
        task_id=task_id,
        experiment_name=experiment,
        load_ground_truth=False,
        max_interactions=config["limits"]["max_requests"],
        max_api_calls_per_interaction=config["limits"]["max_api_calls_per_execution"],
        timeout_seconds=config["limits"]["code_timeout_seconds"],
    )
    try:
        # Control-side identity only; no state records or scoring material reach Actor.
        if (
            config.get("online")
            or generated_task is not None
            or instruction is not None
        ):
            binding["initial_state_hash"] = digest(
                {
                    "task_id": task_id,
                    "model_hashes": world.models.model_hashes(),
                    "datetime": str(world.task.datetime),
                }
            )
            binding["original_instruction"] = world.task.instruction
            binding["trajectory_id"] = str(directory)
            binding["generated_task_id"] = (
                generated_task["task_id"] if generated_task else None
            )
        if instruction is not None:
            rows = world.models["supervisor"]["Task"].find_all()
            if len(rows) != 1 or rows[0].status is not None:
                raise ValueError("Derived task requires clean active initial state")
            rows[0].instruction = instruction
            rows[0].save()
            world.task.instruction = instruction
        episode = loop(world, client, config, directory)
        world.save()
        api_calls = list(world.requester.request_tracker.requests)
        write_json(directory / "api_calls.json", api_calls)
        episode["api_counts"] = {
            "appworld_executions": len(episode["actions"]),
            "documentation": sum("/api_docs/" in r["url"] for r in api_calls),
            "business": sum(
                "/api_docs/" not in r["url"] and "/supervisor/" not in r["url"]
                for r in api_calls
            ),
            "supervisor": sum("/supervisor/" in r["url"] for r in api_calls),
        }
        if generated_task is not None or instruction is not None:
            terminal = world.requester.request(
                "supervisor", "show_active_task", track=False
            )
            episode["coevolve_terminal"] = {
                k: terminal.get(k) for k in ("instruction", "status", "answer")
            }
        episode["status"] = "valid" if episode["fault"] is None else "invalid"
    finally:
        world.close()
    # Full messages are persisted before official evaluation, never amended with labels.
    write_json(directory / "trajectory.json", binding | episode)
    if instruction is not None:
        # Derived public goal is judged independently; never use source official score.
        evaluation = {"status": "pending_judge", "success": None}
    else:
        try:
            evaluation = evaluate_isolated(
                task_id, experiment, root, directory, research=context
            )
        except Exception as exc:
            evaluation = {
                "status": "evaluation_error",
                "success": None,
                "error": repr(exc),
            }
    valid = evaluation["status"] == "valid" and episode["fault"] is None
    result = {
        "task_id": task_id,
        "attempt": attempt,
        "success": evaluation["success"] if valid else None,
        "valid": valid,
        "evaluation": evaluation,
        "termination": episode["termination"],
        "eligible": valid
        and evaluation["success"] is True
        and not episode["format_rejections"],
        "usage": episode["usage"],
        "api_counts": episode["api_counts"],
        "seconds": now() - started,
        "trajectory_sha256": digest(binding | episode),
    }
    write_json(directory / "result.json", result)
    return result
