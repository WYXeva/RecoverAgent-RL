"""Thin v2 configuration boundary over the existing Teacher takeover loop."""

import copy
import math

from .io import read_json
from .v2_tasks import ROOT, RUNTIME, context_for


def configuration(task_id, identity, root, remaining_cost, *, recovery=True):
    frozen = read_json(ROOT / "configs/v2_round1_cpu_offload_20261109.json")
    config = copy.deepcopy(
        read_json(ROOT / frozen["teacher_config_template"])["teacher_config"]
    )
    config.update(
        v2_runtime=RUNTIME,
        custom_research=context_for(task_id, training=True),
        appworld_root=str(root),
        student=identity,
        teacher=copy.deepcopy(frozen["teacher"]),
        protocol_lock=frozen["source_lock"],
    )
    if not recovery:
        config.pop("recovery_protocol")
    config["authorization"] = {
        "approved": True,
        "currency": "CNY",
        "max_cost": remaining_cost,
        "reference": "User-authorized v2 round1; remaining shared run ledger cap",
    }
    config["limits"]["episode_cost"] = remaining_cost
    config.update(
        remaining_cost=remaining_cost,
        remaining_requests=80,
        remaining_generated_tokens=16384,
    )
    return config


def validate_config(config):
    from .v2_model import validate_identity
    from .v2_runtime import verify_lock

    verify_lock()
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
    )
    if config != expected:
        raise ValueError("v2 Teacher configuration changed")
