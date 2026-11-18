"""Atomic controller state and immutable operation receipts for S2 continuation."""

import json
import os
from pathlib import Path

from .io import digest, read_json, write_json


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def evidence(path, value):
    """Idempotent only when the exact previous evidence is present."""
    path = Path(path)
    if path.exists():
        if read_json(path) != value:
            raise ValueError("Existing S2 evidence changed: " + str(path))
    else:
        write_json(path, value)


def protocol_identity():
    from .v2_runtime import s2_execution
    from .coevolve_llm import call_identity

    settings = s2_execution()
    return {
        "execution": settings["revision"],
        "completion": settings.get("completion_contract_revision", "legacy"),
        "reward": settings.get(
            "reward_protocol_revision", settings.get("public_goal_revision", "legacy")
        ),
        "native_parameters": settings.get("native_parameter_revision", "legacy"),
        **(
            {"tool_discovery": settings["tool_discovery_revision"]}
            if settings.get("tool_discovery_revision")
            else {}
        ),
        **({"judge": call_identity()} if settings.get("judge_text") else {}),
        **(
            {"student_input_encoding": settings["student_input_encoding"]}
            if settings.get("student_input_encoding")
            else {}
        ),
    }


def binding(config, inputs):
    return digest(
        {"config": config, "inputs": inputs, "protocols": protocol_identity()}
    )


def unknown_episode(task, policy, directory, reason, seed, limits):
    """Keep an interrupted slot unknown. Never replay its already dispatched requests."""
    directory = Path(directory)
    requests = [
        read_json(p)
        for p in sorted((directory / "session/requests").glob("*.request.json"))
    ]
    responses = [
        read_json(p)
        for p in sorted((directory / "session/requests").glob("*.response.json"))
    ]
    return {
        "task_id": task["task_id"],
        "generated_task_id": task.get("generated_task", {}).get("task_id"),
        "policy": policy,
        "initial_state_hash": None,
        "directory": str(directory),
        "success": None,
        "classification": "infrastructure_error",
        "purpose": "grpo",
        "termination": "interrupted_operation",
        "fault": {"message": reason},
        "seed": seed,
        "usage": {
            "model_requests": len(requests),
            "prompt_tokens": sum(len(r["input_token_ids"]) for r in requests),
            "generated_tokens": sum(len(r["token_ids"]) for r in responses),
            "unknown_generation_reservation": sum(
                r["max_new_tokens"] for r in requests[len(responses) :]
            )
            if requests
            else limits["max_generated_tokens"],
        },
        "costs": {"model_seconds": sum(r["generation_seconds"] for r in responses)},
        "wall_seconds": None,
    }
