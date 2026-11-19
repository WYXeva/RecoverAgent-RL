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


def run(task_id, attempt, directory, root, config, client, *, training=True):
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
    finally:
        world.close()
    # Full messages are persisted before official evaluation, never amended with labels.
    write_json(directory / "trajectory.json", binding | episode)
    try:
        evaluation = evaluate_isolated(
            task_id, experiment, root, directory, research=context
        )
    except Exception as exc:
        evaluation = {"status": "evaluation_error", "success": None, "error": repr(exc)}
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
