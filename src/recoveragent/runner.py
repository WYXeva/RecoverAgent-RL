"""Bounded real Student rollout with controller-only snapshots and official evaluation."""

import copy
import json
import os
import time
from pathlib import Path

from jsonschema import ValidationError

from .adapter import AppWorldAdapter
from .io import digest, validate_history, write_json
from .policy import LocalPolicy, PolicyError, parse_completion
from .state import capture, restore

CONTINUE = "Continue the original task from the current state and visible history. Check what has already been done and avoid duplicating completed operations."
SYSTEM = """You are a tool-using assistant working in AppWorld. Complete the user's task using public tools. Call exactly one function per turn. Use api_docs to discover APIs; requesting show_api_doc makes that function available on the next turn. All business actions, including login, are your responsibility. Use supervisor.complete_task only when finished. Do not invent API names, arguments, credentials or observations."""


def now():
    # freezegun patches time.monotonic/perf_counter; CLOCK_MONOTONIC remains real.
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def run_episode(config, task_id, run_dir, seed, recovery_source=None):
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    trajectory_id = run_dir.name
    mode = "recovery" if recovery_source else "normal"
    budget = config[mode]
    start = now()
    usage = {
        "generation_reservation_tokens": 0,
        "model_turns": 0,
        "requested_tool_calls": 0,
        "dispatched_tool_calls": 0,
        "prompt_tokens": 0,
        "generated_tokens": 0,
    }
    events = []
    messages, tools = [], []
    candidate = None
    adapter = None
    policy_identity = config["policy_identity"]
    base = {
        "worker_pid": os.getpid(),
        "trajectory_id": trajectory_id,
        "task_id": task_id,
        "split": "train",
        "mode": mode,
        "seed": seed,
        "policy": policy_identity,
        "natural": True,
        "rl_eligible": False,
        "rl_ineligible_reason": "P0–P2 has no behavior log probabilities or training bridge",
    }
    write_json(run_dir / "config.json", config)
    try:
        adapter = AppWorldAdapter(
            config["appworld_root"],
            task_id,
            trajectory_id,
            seed=config["environment_seed"],
            timeout=config["tool_timeout_seconds"],
        )
        base["scenario_id"] = adapter.world.task.generator_id
        if recovery_source:
            source, context = restore(adapter, recovery_source)
            messages, tools = context["messages"], context["tools"]
            base["start"] = {
                "checkpoint_id": source["checkpoint_id"],
                "logical_state_hash": adapter.fingerprint(),
                "history_hash": digest(messages),
                "tool_schema_hash": digest(tools),
                "runtime_hash": digest(adapter.runtime()),
            }
            messages.append({"role": "user", "content": config["continuation_prompt"]})
        else:
            messages = [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(adapter.public_task(), ensure_ascii=False),
                },
            ]
            tools = adapter.initial_tools()
        policy = LocalPolicy(
            config["inference_url"], timeout=config["model_timeout_seconds"]
        )
        termination = "budget_exhausted"
        while (
            usage["model_turns"] < budget["max_model_turns"]
            and usage["dispatched_tool_calls"] < budget["max_tool_calls"]
        ):
            left = (
                min(budget["max_generated_tokens"], config["episode_token_allowance"])
                - usage["generated_tokens"]
            )
            if left <= 0 or now() - start >= config["episode_wall_time_seconds"]:
                break
            try:
                usage["generation_reservation_tokens"] = min(
                    config["policy"]["max_new_tokens"], left
                )
                generated = policy.generate(
                    messages,
                    tools,
                    seed + usage["model_turns"],
                    min(config["policy"]["max_new_tokens"], left),
                    config["policy"],
                )
            except PolicyError as exc:
                if str(exc) == "context_budget_exhausted":
                    usage["generation_reservation_tokens"] = 0
                    termination = "context_budget_exhausted"
                    break
                raise
            usage["generation_reservation_tokens"] = 0
            usage["model_turns"] += 1
            usage["prompt_tokens"] += generated["prompt_tokens"]
            usage["generated_tokens"] += generated["generated_tokens"]
            if generated["policy"] != policy_identity:
                raise ValueError("Inference policy identity changed during sampling")
            event = {
                "turn": usage["model_turns"],
                "history_hash": digest(messages),
                "tools": copy.deepcopy(tools),
                "generation": generated,
            }
            try:
                message = parse_completion(
                    generated["text"], f"{trajectory_id}_call_{usage['model_turns']}"
                )
            except (ValueError, KeyError) as exc:
                messages.extend(
                    [
                        {"role": "assistant", "content": generated["text"]},
                        {
                            "role": "user",
                            "content": f"Invalid function call format: {exc}",
                        },
                    ]
                )
                event["format_error"] = str(exc)
            else:
                call = message["tool_calls"][0]
                name, arguments = (
                    call["function"]["name"],
                    call["function"]["arguments"],
                )
                usage["requested_tool_calls"] += 1
                if name == "supervisor__complete_task":
                    candidate = capture(
                        adapter,
                        run_dir / f"pre_termination_{usage['model_turns']}",
                        messages,
                        tools,
                        "pre_termination",
                        {
                            "policy": policy_identity,
                            "config_hash": digest(config),
                            "usage_at_capture": dict(usage),
                        },
                    )
                messages.append(message)
                try:
                    # Validation occurs before dispatch; malformed attempts are not real tool calls.
                    from .adapter import call_code

                    call_code(name, arguments, tools)
                except (ValueError, ValidationError) as exc:
                    result = "Invalid tool call: " + str(exc)
                    event["validation_error"] = result
                else:
                    usage["dispatched_tool_calls"] += 1
                    result, tools = adapter.execute(name, arguments, tools)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": name,
                        "content": result,
                    }
                )
                event["tool_result"] = result
            validate_history(messages)
            events.append(event)
            write_json(
                run_dir / "turns" / f"{usage['model_turns']:03d}.json",
                {"event": event, "messages": messages},
            )
            if adapter.world.task_completed():
                termination = "completion_claim"
                break
        position = (
            "post_action" if termination == "completion_claim" else "budget_exhausted"
        )
        terminal = capture(
            adapter,
            run_dir / "terminal",
            messages,
            tools,
            position,
            {
                "policy": policy_identity,
                "config_hash": digest(config),
                "usage_at_capture": dict(usage),
            },
        )
        candidate = (
            candidate if termination == "completion_claim" and candidate else terminal
        )
        adapter.save_terminal()
        adapter.close()
        adapter = None
        # The official evaluator reloads the original task as its start reference.
        from appworld.evaluator import evaluate_task

        tracker = evaluate_task(
            task_id, experiment_name=trajectory_id, suppress_errors=True
        )
        evaluation = {
            "task_id": task_id,
            "trajectory_id": trajectory_id,
            "success": bool(tracker.success),
            "official": tracker.to_dict(),
            "reference": "original_task",
            "terminal_snapshot": terminal,
        }
        write_json(run_dir / "evaluation.json", evaluation)
        base.update(
            status="valid",
            termination_reason=termination,
            snapshot_ref=candidate,
            evaluation_ref=str(run_dir / "evaluation.json"),
            reward=int(tracker.success),
        )
    except Exception as exc:
        # Infrastructure failures are retained and have no policy reward.
        import traceback

        base.update(
            status="infrastructure_error",
            termination_reason=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
            reward=None,
        )
    finally:
        if adapter is not None:
            try:
                adapter.close()
            except Exception as exc:
                base.update(
                    status="infrastructure_error",
                    reward=None,
                    cleanup_error=type(exc).__name__ + ": " + str(exc),
                )
    usage["wall_seconds"] = now() - start
    base.update(usage=usage, messages=messages, final_tools=tools, events=events)
    write_json(run_dir / "trajectory.json", base)
    return base
