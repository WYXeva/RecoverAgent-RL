"""Versioned public prompts; no task-specific solutions or hidden evaluator data."""

from .runner import SYSTEM
from .tool_modes import DISCOVERY_INSTRUCTION

ACTOR_PROMPTS = ("legacy_v1", "budget_batched_v2")


def actor_system(config):
    system = SYSTEM
    if config.get("tool_mode", "dynamic_discovery") == "api_predictor":
        system = system.replace(
            DISCOVERY_INSTRUCTION,
            "The available APIs were selected before execution. "
            "Their schemas are fixed for this task.",
        )
    batch = (
        "You may request a bounded batch of independent calls. "
        "Calls execute serially in the order requested. Every call "
        "must use an already visible schema and known arguments. "
        "Wait for results before making dependent calls. "
        "If completing the task, complete_task must be the last call."
    )
    if config.get("actor_prompt_variant", "legacy_v1") == "legacy_v1":
        return system.replace("Call exactly one function per turn.", batch)
    batch = (
        "You may request multiple independent tool calls in one response. "
        "Calls execute serially in the order requested. Each call must use an "
        "already visible schema and arguments available before this response. "
        "Wait for real results before making dependent calls. Choose batch sizes "
        "whose complete function names and JSON arguments fit the response output "
        "budget. If more work remains, continue in later responses after receiving "
        "all results; never truncate arguments to fit more calls. Track completed "
        "items to avoid unnecessary repeated reads. Gather the evidence needed "
        "for the user's task before claiming completion. "
        "If completing the task, complete_task must be the last call."
    )
    limits, teacher = config["limits"], config["teacher"]
    quota = []
    for label, maximum in (
        ("tool calls over the task", limits["max_tool_calls"]),
        ("tool calls in one response", teacher.get("max_tool_calls_per_turn", 1)),
    ):
        quota.append(
            f"There is no numeric count quota for {label}."
            if maximum is None
            else f"The maximum number of {label} is {maximum}."
        )
    total = min(
        limits["episode_generated_tokens"], config["remaining_generated_tokens"]
    )
    budget = (
        f" Each model response has at most {teacher['max_new_tokens']} output tokens, "
        f"further bounded by the remaining total output allowance. This task has "
        f"{total} total generated tokens, {config['remaining_requests']} model requests "
        f"(including any pre-execution API prediction), and "
        f"{limits['episode_wall_seconds']} seconds of execution time. "
        "Keep nonessential narration brief; preserve complete calls and task correctness."
    )
    return (
        system.replace("Call exactly one function per turn.", batch)
        + " "
        + " ".join(quota)
        + budget
    )
