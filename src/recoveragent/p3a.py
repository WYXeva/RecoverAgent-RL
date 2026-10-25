"""Bounded, serial Teacher collection from original train states; no training entry."""

import argparse
import copy
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from jsonschema import ValidationError

from .adapter import AppWorldAdapter, call_code
from .io import digest, read_json, write_json
from .runner import now
from .execution_v5 import ACTOR_PROMPTS, actor_system
from .answer_guard import AnswerGuard, PROTOCOL as ANSWER_PROTOCOL
from .validated_selection import initialize_tools
from .teacher_history import capture_terminal, validate_history
from .tool_modes import (
    MODES,
    PREDICTOR_PROMPTS,
    execute_with_mode,
)
from .teacher import (
    TeacherPolicy,
    TeacherTransportError,
    normalize_message,
    preflight,
    read_credentials,
    response_usage,
    usage_cost,
)
from .harness import (
    PROTOCOL as HARNESS_PROTOCOL,
    RULES as HARNESS_RULES,
    RUNTIME_REVISION,
    SCHEMA_REVISION,
    ProtocolHarness,
    capture_interruption,
    record_call_intent,
    failure_category,
    ServiceCircuitBreaker,
    check_batch,
    provider_error_record,
)


def validate_config(config):
    if config.get("harness_protocol") not in {None, HARNESS_PROTOCOL}:
        raise ValueError("Unknown harness protocol")
    if config.get("harness_protocol") and (
        config.get("harness_runtime_revision")
        not in {RUNTIME_REVISION, SCHEMA_REVISION}
        or config.get("actor_prompt_variant") != "execution_v5"
        or config.get("environment_profile") != "appworld_sqlalchemy20_v1"
        or config["teacher"]["max_new_tokens"] != 4096
        or config["limits"]["max_tool_calls"] is not None
        or config["teacher"].get("max_tool_calls_per_turn") is not None
    ):
        raise ValueError("harness_v1 requires declared v5/environment/unlimited tools")
    if (
        config["project_version"]
        not in {"0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "0.10"}
        or config["training_enabled"] is not False
    ):
        raise ValueError("Only versioned P3-A collection is allowed")
    if config.get("tool_mode", "dynamic_discovery") not in MODES:
        raise ValueError("Unknown tool mode")
    if config.get("actor_prompt_variant", "legacy_v1") not in ACTOR_PROMPTS:
        raise ValueError("Unknown Actor prompt variant")
    if (
        config.get("predictor_prompt_variant", "official_zero_shot")
        not in PREDICTOR_PROMPTS
    ):
        raise ValueError("Unknown Predictor prompt variant")
    if config["student"]["model"] != "Qwen/Qwen3-4B":
        raise ValueError("Formal Student must be Qwen3-4B")
    from .validated_selection import PROTOCOL, LEGACY_PROTOCOL

    selection = config.get("selection_protocol", LEGACY_PROTOCOL)
    if selection not in {PROTOCOL, LEGACY_PROTOCOL}:
        raise ValueError("Unknown selection protocol")
    if selection == PROTOCOL and (
        (config["project_version"], config.get("actor_prompt_variant"))
        not in {("0.9", "execution_v4"), ("0.10", "execution_v5")}
        or config.get("predictor_prompt_variant") != "dependency_examples_v2"
    ):
        raise ValueError("Validated selection requires the v0.9 prompt protocol")
    if config.get("answer_guard_protocol") not in {None, ANSWER_PROTOCOL}:
        raise ValueError("Unknown answer guard protocol")
    if (config.get("actor_prompt_variant") == "execution_v5") != (
        config.get("answer_guard_protocol") == ANSWER_PROTOCOL
    ) or (config.get("answer_guard_protocol") and config["project_version"] != "0.10"):
        raise ValueError("v5 requires the declared v0.10 answer guard")
    limits = config["limits"]
    caps = {
        "max_tasks": 2,
        "max_requests": 80,
        "total_generated_tokens": 32768,
        "max_model_turns": 80,
        "episode_generated_tokens": 16384,
        "episode_wall_seconds": 900,
        "total_wall_seconds": 1800,
        "tool_timeout_seconds": 60,
        "repeated_identical_call_limit": 3,
    }
    profile = config.get("collection_profile", "pilot")
    if profile not in {"pilot", "scenario_expansion_v1"}:
        raise ValueError("Unknown collection profile")
    if profile == "scenario_expansion_v1":
        from .protocol_lock import verify_lock

        if config["project_version"] not in {"0.8", "0.9", "0.10"}:
            raise ValueError("Expanded collection requires v0.8 or later")
        verify_lock(config)
        caps.update(
            max_tasks=20,
            max_requests=800,
            total_generated_tokens=327680,
            total_wall_seconds=18000,
        )
    for key, cap in caps.items():
        if type(limits[key]) is not int or not 1 <= limits[key] <= cap:
            raise ValueError(f"Pilot limit out of range: {key}")
    if limits["max_infrastructure_retries"] != 0:
        raise ValueError("P3-A pilot has no automatic paid retries")
    teacher = config["teacher"]
    for value in (limits["max_tool_calls"], teacher.get("max_tool_calls_per_turn", 1)):
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError(
                "Tool count limits must be null (unlimited) or positive integers"
            )
    for key in (
        "timeout_seconds",
        "max_new_tokens",
        "billing_context_tokens",
        "max_request_bytes",
    ):
        if type(teacher[key]) is not int or teacher[key] <= 0:
            raise ValueError(f"Invalid Teacher limit: {key}")
    if "remaining_requests" in config:
        for key, upper in (
            ("remaining_requests", limits["max_requests"]),
            ("remaining_generated_tokens", limits["total_generated_tokens"]),
            ("remaining_cost", config["authorization"]["max_cost"]),
        ):
            if not 0 <= config[key] <= upper:
                raise ValueError("Worker cannot exceed parent authorization")


def official_evaluate(task_id, experiment_name):
    from appworld.evaluator import evaluate_task

    tracker = evaluate_task(
        task_id, experiment_name=experiment_name, suppress_errors=True
    )
    return bool(tracker.success), tracker.to_dict()


def interrupted_usage(directory, teacher):
    """Recover known usage from durable receipts and reserve only unknown requests."""
    directory = Path(directory)
    totals = {
        "provider_requests": 0,
        "prompt_tokens": 0,
        "generated_tokens": 0,
        "cached_prompt_tokens": 0,
        "tool_calls": len(list((directory / "dispatches").glob("*.json"))),
        "usage_priced_cost": 0.0,
        "unknown_cost_reservation": 0.0,
        "unknown_generation_reservation": 0,
        "usage_known": True,
        "billed_cost": None,
        "currency": teacher["pricing"]["currency"],
    }
    feedback = list((directory / "harness_feedback").glob("*.json"))
    intents = list((directory / "call_intents").glob("*.json"))
    if intents:
        completed = totals["tool_calls"]
        totals["tool_calls"] = max(completed, len(intents))
        totals["tool_results_missing"] = max(0, len(intents) - completed)
    worker_path = directory.parent / (directory.name + ".config.json")
    guarded = worker_path.exists() and read_json(worker_path).get(
        "answer_guard_protocol"
    )
    if feedback or guarded:
        totals["harness_rejections"] = len(feedback)
        totals["tool_requests"] = totals["tool_calls"] + len(feedback)
    if worker_path.exists() and read_json(worker_path).get("harness_protocol"):
        events, unreadable = [], 0
        for path in (directory / "turns").glob("*.json"):
            try:
                events.append(read_json(path)["event"])
            except (OSError, ValueError, KeyError, TypeError):
                unreadable += 1  # A killed write must not hide billing reservations.
        totals["protocol_rejections"] = sum("protocol_rejection" in e for e in events)
        totals["harness_notices"] = sum("harness_notice" in e for e in events)
        totals["unreadable_turn_records"] = unreadable
        totals["harness_counters_lower_bound"] = True
    for path in sorted((directory / "requests").glob("*.reservation.json")):
        totals["provider_requests"] += 1
        reservation = read_json(path)
        response = path.with_name(path.name.replace(".reservation.", ".response."))
        try:
            usage = response_usage(read_json(response))
        except (OSError, ValueError, KeyError, TypeError):
            totals["unknown_cost_reservation"] += reservation["max_cost"]
            totals["unknown_generation_reservation"] += reservation["max_tokens"]
            totals["usage_known"] = False
        else:
            for key in ("prompt_tokens", "generated_tokens", "cached_prompt_tokens"):
                totals[key] += usage[key]
            totals["usage_priced_cost"] += usage_cost(usage, teacher["pricing"])
    return totals


def run_teacher_episode(config, task_id, output, credentials):
    """Production path always uses a real AppWorldAdapter and a real TeacherPolicy."""
    validate_config(config)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = now()
    limits, teacher = config["limits"], config["teacher"]
    usage = {
        "provider_requests": 0,
        "model_turns": 0,
        "tool_calls": 0,
        "prompt_tokens": 0,
        "generated_tokens": 0,
        "cached_prompt_tokens": 0,
        "usage_priced_cost": 0.0,
        "unknown_cost_reservation": 0.0,
        "unknown_generation_reservation": 0,
        "billed_cost": None,
        "currency": teacher["pricing"]["currency"],
    }
    trajectory = {
        "format_version": "p3a-1",
        "trajectory_id": output.name,
        "task_id": task_id,
        "split": "train",
        "mode": "teacher_normal",
        "tool_mode": config.get("tool_mode", "dynamic_discovery"),
        "actor_prompt_variant": config.get("actor_prompt_variant", "legacy_v1"),
        "predictor_prompt_variant": config.get(
            "predictor_prompt_variant", "official_zero_shot"
        ),
        "actor_role": "teacher",
        "start_kind": "original_initial",
        "teacher": copy.deepcopy(teacher),
        "protocol": (
            "native_serial_complete_boundary_no_call_count_quota_v2"
            if limits["max_tool_calls"] is None
            and teacher.get("max_tool_calls_per_turn", 1) is None
            else "native_bounded_batch_serial_complete_boundary_v1"
        ),
        "worker_pid": os.getpid(),
        "training_started": False,
        "status": "infrastructure_error",
        "reward": None,
        "messages": [],
        "turns": [],
        "usage": usage,
        "constraint_check": "official_terminal_checks_only; other safety NOT_MEASURED",
    }
    adapter, guard = None, None
    batch_open, event = False, None
    evaluation_started = False
    harness = (
        ProtocolHarness(
            trajectory["tool_mode"], revision=config.get("harness_runtime_revision")
        )
        if config.get("harness_protocol")
        else None
    )
    if harness:
        trajectory["harness"] = harness.summary()
        usage.update(protocol_rejections=0, harness_notices=0)
    if "selection_protocol" in config:
        trajectory["selection_protocol"] = config["selection_protocol"]
    messages = trajectory["messages"]
    try:
        if harness:
            from .evaluation import environment_check

            trajectory["environment_check"] = environment_check()
        policy = TeacherPolicy(config, credentials)
        adapter = AppWorldAdapter(
            config["appworld_root"],
            task_id,
            output.name,
            seed=config["environment_seed"],
            timeout=limits["tool_timeout_seconds"],
            # Upstream requires an int and compares it directly (None is invalid).
            # This compatibility sentinel removes its former hidden 100-execute
            # ceiling; token/request/time budgets remain the operational bounds.
            max_interactions=sys.maxsize,
        )
        trajectory["scenario_id"] = adapter.world.task.generator_id
        trajectory["appworld"] = adapter.versions
        trajectory["initial_state_hash"] = adapter.fingerprint()
        trajectory["environment_execution_limit"] = sys.maxsize
        messages.extend(
            [
                {
                    "role": "system",
                    "content": actor_system(config)
                    + ("\n" + HARNESS_RULES if harness else ""),
                },
                {
                    "role": "user",
                    "content": json.dumps(adapter.public_task(), ensure_ascii=False),
                },
            ]
        )
        if config.get("answer_guard_protocol"):
            guard = AnswerGuard(adapter.public_task()["instruction"])
            trajectory["answer_guard"] = guard.summary()
            usage.update(tool_requests=0, harness_rejections=0)
        tools, prediction_valid = initialize_tools(
            adapter, policy, config, output, usage, trajectory
        )
        trajectory["initial_tools"] = copy.deepcopy(tools)
        termination = "budget_exhausted" if prediction_valid else "invalid_prediction"
        backend = (
            tuple(trajectory.get("observed_backend") or config.get("expected_backend"))
            if trajectory.get("observed_backend") or config.get("expected_backend")
            else None
        )
        for index in range(limits["max_model_turns"] if prediction_valid else 0):
            if harness and adapter.world.task_completed():
                termination = "completion_claim"
                break
            left = (
                min(
                    limits["episode_generated_tokens"],
                    config["remaining_generated_tokens"],
                )
                - usage["generated_tokens"]
            )
            if (
                left <= 0
                or (
                    limits["max_tool_calls"] is not None
                    and usage["tool_calls"] >= limits["max_tool_calls"]
                )
                or usage["provider_requests"] >= config["remaining_requests"]
                or now() - started >= limits["episode_wall_seconds"]
            ):
                break
            max_tokens = min(teacher["max_new_tokens"], left)
            # Conservative cost reservation uses the full provider context window,
            # not an unverified local estimate of the provider tokenizer.
            reserve = usage_cost(
                {
                    "prompt_tokens": teacher["billing_context_tokens"],
                    "generated_tokens": max_tokens,
                    "cached_prompt_tokens": 0,
                },
                teacher["pricing"],
            )
            if usage["usage_priced_cost"] + reserve > config["remaining_cost"]:
                termination = "cost_budget_exhausted"
                break
            request_id = f"{index + 1:03d}"
            event = {
                "assistant_index": len(messages),
                "history_hash": digest(messages),
                "tools": copy.deepcopy(tools),
                "request_id": request_id,
            }
            payload = policy.build_request(messages, tools, max_tokens)
            write_json(output / "requests" / f"{request_id}.request.json", payload)
            # Persist the reservation before entering a possibly interrupted request.
            write_json(
                output / "requests" / f"{request_id}.reservation.json",
                {
                    "max_tokens": max_tokens,
                    "max_cost": reserve,
                    "history_hash": event["history_hash"],
                    "tools_hash": digest(tools),
                },
            )
            usage["unknown_cost_reservation"] = reserve
            usage["unknown_generation_reservation"] = max_tokens
            usage["provider_requests"] += 1
            response = policy.transport(payload)
            write_json(output / "requests" / f"{request_id}.response.json", response)
            event["request_hash"], event["response_hash"] = (
                digest(payload),
                digest(response),
            )
            current_usage = response_usage(response)
            for key in ("prompt_tokens", "generated_tokens", "cached_prompt_tokens"):
                usage[key] += current_usage[key]
            usage["usage_priced_cost"] += usage_cost(current_usage, teacher["pricing"])
            usage["unknown_cost_reservation"] = 0.0
            usage["unknown_generation_reservation"] = 0
            usage["model_turns"] += 1
            event["usage"] = current_usage
            event["response_model"] = response.get("model")
            event["system_fingerprint"] = response.get("system_fingerprint")
            trajectory["turns"].append(event)
            if (
                current_usage["generated_tokens"] > max_tokens
                or current_usage["prompt_tokens"] > teacher["billing_context_tokens"]
            ):
                raise ValueError("Provider exceeded configured billing bound")
            current_backend = (
                response.get("model"),
                response.get("system_fingerprint"),
            )
            if current_backend[0] != teacher["model"] or not current_backend[1]:
                raise ValueError("Unverifiable Teacher response identity")
            if backend is not None and backend != current_backend:
                raise ValueError("Teacher backend identity changed during the episode")
            backend = current_backend
            trajectory["observed_backend"] = list(backend)
            try:
                message = normalize_message(
                    response, teacher.get("max_tool_calls_per_turn", 1)
                )
                if harness:
                    check_batch(message, messages, tools, require_action=True)
                # Check IDs against the existing history before any dispatch.
                calls = message.get("tool_calls", [])
                if calls and not harness:
                    validate_history(
                        messages
                        + [message]
                        + [
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": "",
                            }
                            for call in calls
                        ]
                    )
                    if (
                        limits["max_tool_calls"] is not None
                        and len(calls) + usage["tool_calls"] > limits["max_tool_calls"]
                    ):
                        raise ValueError("Entire batch exceeds remaining tool budget")
                    for offset, call in enumerate(calls):
                        function = call["function"]
                        call_code(function["name"], function["arguments"], tools)
                        if (
                            function["name"] == "supervisor__complete_task"
                            and offset != len(calls) - 1
                        ):
                            raise ValueError("Completion must be the last batch call")
            except (ValueError, KeyError, TypeError, ValidationError) as exc:
                if harness:
                    event.update(
                        harness.rejection_event(
                            exc,
                            truncated=any(
                                c.get("finish_reason") == "length"
                                for c in response.get("choices", [])
                            ),
                            guard=guard,
                        )
                    )
                    feedback = event["feedback"]
                    usage["protocol_rejections"] += 1
                    messages.append(feedback)
                    write_json(
                        output / "turns" / f"{request_id}.json",
                        {"event": event, "messages": messages},
                    )
                    if harness.stopped:
                        termination = "protocol_correction_exhausted"
                        break
                    continue
                event["format_error"] = type(exc).__name__ + ": " + str(exc)
                termination = "invalid_completion"
                break
            messages.append(message)
            verdicts = guard.inspect(calls) if guard else [None] * len(calls)
            if guard:
                event.update(
                    harness_verdicts=verdicts,
                    supervise=not any(verdicts),
                    appworld_dispatched_count=0,
                )
            if not calls:
                termination = (
                    "answer_correction_exhausted"
                    if guard and guard.stopped
                    else "assistant_stop_without_tool"
                )
                if guard:
                    event.update(tool_results=[], dispatched_count=0)
                break
            event.update(tool_results=[], dispatched_count=0)
            batch_open = True
            for call, verdict in zip(calls, verdicts):
                if harness and now() - started >= limits["episode_wall_seconds"]:
                    raise TimeoutError("Batch interrupted by wall-clock budget")
                name = call["function"]["name"]
                arguments = call["function"]["arguments"]
                if guard:
                    usage["tool_requests"] += 1
                if verdict is None:
                    if harness:
                        record_call_intent(output, event, call)
                    usage["tool_calls"] += 1
                    result, tools = execute_with_mode(
                        adapter, name, arguments, tools, trajectory["tool_mode"]
                    )
                    event.pop("inflight_call_id", None)
                    if guard:
                        event["appworld_dispatched_count"] += 1
                else:
                    from .io import canonical

                    result = canonical(verdict)
                    usage["harness_rejections"] += 1
                observation = {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": result,
                }
                messages.append(observation)
                event["tool_results"].append(copy.deepcopy(observation))
                event["dispatched_count"] += 1
                # Preserve completed calls even if a later batch call is interrupted.
                write_json(
                    output
                    / ("dispatches" if verdict is None else "harness_feedback")
                    / f"{request_id}_{len(event['tool_results']):03d}.json",
                    observation,
                )
            validate_history(messages)
            batch_open = False
            if (
                harness
                and not any(verdicts)
                and not any(
                    c["function"]["name"] == "supervisor__complete_task" for c in calls
                )
            ):
                notice = harness.notice(messages)
                if notice:
                    event["harness_notice"] = notice
                    messages.append(notice)
                    usage["harness_notices"] += 1
            write_json(
                output / "turns" / f"{request_id}.json",
                {"event": event, "messages": messages},
            )
            if guard and guard.stopped:
                termination = "answer_correction_exhausted"
                break
            if adapter.world.task_completed():
                termination = "completion_claim"
                break
        validate_history(messages)
        if guard:
            trajectory["answer_guard"] = guard.summary()
        trajectory["terminal_checkpoint_id"] = capture_terminal(
            adapter,
            output / "terminal",
            messages,
            tools,
            {
                "actor_role": "teacher",
                "mode": "teacher_normal",
                "config_hash": digest(config),
            },
        )
        adapter.save_terminal()
        adapter.close()
        adapter = None
        evaluation_started = bool(harness)
        if harness:
            from .evaluation import evaluate_isolated

            evaluated = evaluate_isolated(
                task_id, output.name, config["appworld_root"], output
            )
            if evaluated["status"] != "valid":
                trajectory.update(
                    evaluation_error=evaluated["exception_types"],
                    termination_reason="evaluation_error",
                )
                raise RuntimeError("Official evaluation encountered runtime errors")
            success, official = evaluated["success"], evaluated["official"]
        else:
            success, official = official_evaluate(task_id, output.name)
        write_json(
            output / "evaluation.json",
            {
                "task_id": task_id,
                "trajectory_id": output.name,
                "success": success,
                "reference": "original_task",
                "evaluator": "appworld.evaluator.evaluate_task",
                "terminal_checkpoint_id": trajectory["terminal_checkpoint_id"],
                "official": official,
            },
        )
        trajectory.update(
            status="valid", reward=int(success), termination_reason=termination
        )
    except Exception as exc:
        # Do not include provider bodies/credential-bearing exception strings.
        trajectory.update(
            error_type=type(exc).__name__,
            termination_reason=(
                "evaluation_error"
                if evaluation_started or "evaluation_error" in trajectory
                else "infrastructure_error"
            ),
        )
        if harness:
            if isinstance(exc, TeacherTransportError):
                trajectory["provider_error"] = provider_error_record(exc)
            if batch_open:
                event.update(status="interrupted_batch", error_type=type(exc).__name__)
                trajectory["termination_reason"] = "interrupted_batch"
                trajectory["interruption"] = capture_interruption(
                    adapter, output, messages, tools, event
                )
                write_json(
                    output / "turns" / f"{event['request_id']}.json",
                    {"event": event, "messages": messages},
                )
    finally:
        if harness:
            trajectory["harness"] = harness.summary()
        if guard is not None:
            trajectory["answer_guard"] = guard.summary()
        if adapter is not None:
            try:
                adapter.close()
            except Exception as exc:
                trajectory.update(
                    status="infrastructure_error",
                    reward=None,
                    cleanup_error=type(exc).__name__,
                )
    usage["wall_seconds"] = now() - started
    if harness:
        trajectory["failure_category"] = failure_category(trajectory)
    write_json(output / "trajectory.json", trajectory)
    return trajectory


def collect(config, output, credentials_file=None):
    validate_config(config)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config.json", config)
    check = preflight(config, read_credentials(credentials_file))
    write_json(output / "preflight.json", check)
    if check["status"] == "BLOCKED":
        summary = {
            "status": "BLOCKED",
            "reasons": check["reasons"],
            "attempts": 0,
            "provider_requests": 0,
            "successful_tasks": 0,
            "success_rate": None,
            "training_started": False,
            "actual_cost": 0,
        }
        write_json(output / "summary.json", summary)
        return summary
    if config.get("harness_protocol"):
        from .evaluation import environment_check

        write_json(output / "environment_check.json", environment_check())
    from appworld import load_task_ids, update_root

    update_root(str(Path(config["appworld_root"]).resolve()))
    expanded = config.get("collection_profile") == "scenario_expansion_v1"
    plan = None
    if expanded:
        from .sampling import load_plan
        from .protocol_lock import verify_lock

        plan = load_plan(config)
        write_json(output / "task_manifest.json", plan)
        write_json(output / "protocol_lock.json", read_json(config["protocol_lock"]))
        write_json(output / "protocol_check.json", {"lock_hash": verify_lock(config)})
        tasks = plan["tasks"]
    else:
        tasks = load_task_ids("train")[: config["limits"]["max_tasks"]]
    comparison = config.get("compare_tool_modes", False)
    schedule = [
        (task, mode)
        for index, task in enumerate(tasks)
        for mode in (
            (MODES if index % 2 == 0 else tuple(reversed(MODES)))
            if comparison
            else (config.get("tool_mode", "dynamic_discovery"),)
        )
    ]
    write_json(
        output / "task_selection.json",
        {
            "split": "train",
            "order": "frozen_scenario_manifest" if expanded else "official",
            "tasks": tasks,
            "schedule": schedule,
            "mode_order": "counterbalanced" if comparison else "single",
            "selection_before_results": True,
        },
    )
    attempts = []
    breaker = ServiceCircuitBreaker() if config.get("harness_protocol") else None
    started = now()
    limits = config["limits"]
    for task_id, tool_mode in schedule:
        remaining_wall = limits["total_wall_seconds"] - (now() - started)
        if remaining_wall <= 0:
            break
        worker_config = copy.deepcopy(config)
        worker_config["tool_mode"] = tool_mode
        worker_config.update(
            remaining_generated_tokens=limits["total_generated_tokens"]
            - sum(
                x["usage"]["generated_tokens"]
                + x["usage"].get("unknown_generation_reservation", 0)
                for x in attempts
            ),
            remaining_requests=limits["max_requests"]
            - sum(x["usage"]["provider_requests"] for x in attempts),
            remaining_cost=config["authorization"]["max_cost"]
            - sum(
                x["usage"]["usage_priced_cost"]
                + x["usage"].get("unknown_cost_reservation", 0)
                for x in attempts
            ),
        )
        if comparison or expanded:
            # Reserve equal end-to-end allowances for every arm, including selector.
            worker_config["remaining_generated_tokens"] = min(
                worker_config["remaining_generated_tokens"],
                limits["total_generated_tokens"] // len(schedule),
            )
            worker_config["remaining_requests"] = min(
                worker_config["remaining_requests"],
                limits["max_requests"] // len(schedule),
            )
            worker_config["remaining_cost"] = min(
                worker_config["remaining_cost"],
                config["authorization"]["max_cost"] / len(schedule),
            )
            worker_config["limits"]["episode_generated_tokens"] = min(
                limits["episode_generated_tokens"],
                worker_config["remaining_generated_tokens"],
            )
            worker_config["limits"]["episode_wall_seconds"] = min(
                limits["episode_wall_seconds"],
                limits["total_wall_seconds"] // len(schedule),
            )
        if attempts and attempts[0].get("observed_backend"):
            worker_config["expected_backend"] = attempts[0]["observed_backend"]
        if config.get("harness_protocol"):
            worker_config["remaining_requests"] = min(
                worker_config["remaining_requests"], limits.get("episode_requests", 40)
            )
            worker_config["remaining_cost"] = min(
                worker_config["remaining_cost"], limits.get("episode_cost", 1)
            )
        if (
            min(
                worker_config[k]
                for k in (
                    "remaining_generated_tokens",
                    "remaining_requests",
                    "remaining_cost",
                )
            )
            <= 0
        ):
            break
        name = "teacher_" + uuid.uuid4().hex[:12]
        cfg = output / f"{name}.config.json"
        write_json(cfg, worker_config)
        command = [
            sys.executable,
            "-m",
            "recoveragent.p3a",
            "worker",
            "--config",
            str(cfg),
            "--output",
            str(output / name),
            "--task",
            task_id,
        ]
        if credentials_file:
            command += ["--credentials-file", str(Path(credentials_file).resolve())]
        try:
            with (output / f"{name}.log").open("x") as log:
                result = subprocess.run(
                    command,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=min(
                        worker_config["limits"]["episode_wall_seconds"] + 30,
                        remaining_wall,
                    ),
                )
            if result.returncode:
                raise RuntimeError("Worker failed")
            trajectory = read_json(output / name / "trajectory.json")
        except (subprocess.TimeoutExpired, RuntimeError, FileNotFoundError) as exc:
            # Preserve unknown billing reservations; expanded jobs may continue
            # with another isolated task, never retry the failed task implicitly.
            write_json(
                output / f"{name}.controller_error.json",
                {"error_type": type(exc).__name__, "worker_completed": False},
            )
            trajectory = {
                "trajectory_id": name,
                "task_id": task_id,
                "split": "train",
                "status": "infrastructure_error",
                "reward": None,
                "termination_reason": "worker_interrupted",
                "actor_role": "teacher",
                "tool_mode": tool_mode,
                "usage": interrupted_usage(output / name, config["teacher"]),
            }
            if (output / name / "trajectory.json").exists():
                # A failure after the append-only terminal record must not overwrite it.
                trajectory = read_json(output / name / "trajectory.json")
            else:
                write_json(output / name / "trajectory.json", trajectory)
            attempts.append(trajectory)
            if breaker and breaker.observe(trajectory):
                break
            if expanded and config.get("continue_after_infrastructure_error", False):
                continue
            break
        attempts.append(trajectory)
        if breaker and breaker.observe(trajectory):
            break
        if trajectory["status"] == "infrastructure_error" and not (
            expanded and config.get("continue_after_infrastructure_error", False)
        ):
            break
    from .demonstrations import export_dataset

    manifest = export_dataset(
        output, output / "clean_dataset", set(load_task_ids("train")), limits
    )
    summary = {
        "status": "PASS" if manifest["counts"]["accepted"] else "PARTIAL",
        "attempts": len(attempts),
        "dataset_id": manifest["dataset_id"],
        "counts": manifest["counts"],
        "costs": manifest["costs"],
        "wall_seconds": now() - started,
        "training_started": False,
        "teacher_capability": "pilot_success_observed"
        if manifest["counts"]["accepted"]
        else "NOT_ESTABLISHED",
    }
    if breaker:
        summary["service_stop_reason"] = breaker.reason
        summary["unattempted_schedule"] = schedule[len(attempts) :]
    write_json(output / "summary.json", summary)
    if comparison:
        from .compare_tools import summarize_comparison

        write_json(output / "comparison.json", summarize_comparison(output))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["preflight", "collect", "compare", "worker"]
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--credentials-file")
    parser.add_argument("--task")
    args = parser.parse_args()
    config = read_json(args.config)
    if args.command == "compare":
        config["compare_tool_modes"] = True
    validate_config(config)
    if args.command == "preflight":
        result = preflight(config, read_credentials(args.credentials_file))
        write_json(args.output, result)
    elif args.command == "worker":
        if not args.task:
            parser.error("--task is required for worker")
        result = run_teacher_episode(
            config, args.task, args.output, read_credentials(args.credentials_file)
        )
        result = {k: result[k] for k in ("status", "trajectory_id", "reward", "usage")}
    else:
        result = collect(config, args.output, args.credentials_file)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
