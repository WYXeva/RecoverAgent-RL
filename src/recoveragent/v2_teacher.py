"""Thin v2 configuration boundary over the existing Teacher takeover loop."""

import copy
import math

from .io import read_json
from .v2_context import CONFIG, sequence_limit
from .v2_tasks import ROOT, RUNTIME, context_for


def configuration(
    task_id,
    identity,
    root,
    remaining_cost,
    *,
    recovery=True,
    runtime=RUNTIME,
    purpose=None,
):
    from .v2_runtime import (
        S1_RUNTIME,
        S1_SHORT_RUNTIME,
        S1_SHORT_VLLM_RUNTIME,
        S2_RUNTIME,
    )

    if runtime not in {
        RUNTIME,
        S1_RUNTIME,
        S1_SHORT_RUNTIME,
        S1_SHORT_VLLM_RUNTIME,
        S2_RUNTIME,
    }:
        raise ValueError("Unknown Teacher learning runtime")
    frozen = read_json(
        ROOT / f"configs/{runtime}.json" if runtime != RUNTIME else CONFIG
    )
    config = copy.deepcopy(
        read_json(ROOT / frozen["teacher_config_template"])["teacher_config"]
    )
    config.update(
        v2_runtime=runtime,
        custom_research=context_for(task_id, training=True),
        appworld_root=str(root),
        student=identity,
        teacher=copy.deepcopy(frozen["teacher"]),
        protocol_lock=frozen["source_lock"],
        learning_context={"max_sequence_tokens": sequence_limit(frozen)},
    )
    if not recovery:
        config.pop("recovery_protocol")
    if runtime == S2_RUNTIME:
        from .v2_runtime import s2_execution

        revision = s2_execution().get("completion_contract_revision")
        if revision:
            config["teacher"]["completion_contract_revision"] = revision
    if purpose is not None:
        if (
            runtime != S2_RUNTIME
            or recovery
            or purpose not in {"s2_text", "s2_exploration", "s2_solver"}
        ):
            raise ValueError("Independent Teacher context is scoped to S2 CoEvolve")
        settings = s2_execution().get("teacher_context")
        if settings:
            config["teacher_purpose"] = purpose
            config["teacher_context"] = copy.deepcopy(settings)
    config["authorization"] = {
        "approved": True,
        "currency": frozen["teacher"]["pricing"]["currency"],
        "max_cost": remaining_cost,
        "reference": "User-authorized v2 round1; remaining shared run ledger cap",
    }
    config["limits"]["episode_cost"] = remaining_cost
    if frozen.get("environment_contract"):
        config["environment_contract"] = frozen["environment_contract"]
    config.update(
        remaining_cost=remaining_cost,
        remaining_requests=80,
        remaining_generated_tokens=16384,
    )
    return config


def validate_config(config):
    from .v2_model import validate_identity
    from .v2_runtime import verify_lock

    verify_lock(config["v2_runtime"])
    validate_identity(config["student"])
    cost = config["authorization"]["max_cost"]
    if type(cost) not in (int, float) or not math.isfinite(cost) or cost <= 0:
        raise ValueError("A positive remaining Teacher budget is required")
    expected = configuration(
        config["custom_research"]["task_id"],
        config["student"],
        config["appworld_root"],
        cost,
        recovery=bool(config.get("recovery_protocol")),
        runtime=config["v2_runtime"],
        purpose=config.get("teacher_purpose"),
    )
    if config != expected:
        raise ValueError("v2 Teacher configuration changed")
