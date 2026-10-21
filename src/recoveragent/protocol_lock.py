"""Check the frozen cc53bea prompt/tool contracts before expanded collection."""

import hashlib
from pathlib import Path

from .io import digest, read_json


def verify_lock(config, root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    lock = read_json(root / config["protocol_lock"])
    for name, expected in lock["files"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen prompt/tool protocol changed: " + name)
    if (
        config.get("actor_prompt_variant") != lock["actor_prompt_variant"]
        or config.get("predictor_prompt_variant") != lock["predictor_prompt_variant"]
        or config.get("selection_protocol", "frozen_v3")
        != lock.get("selection_protocol", "frozen_v3")
        or config.get("answer_guard_protocol") != lock.get("answer_guard_protocol")
        or config["teacher"]["max_new_tokens"] != 4096
        or config["limits"]["max_tool_calls"] is not None
        or config["teacher"]["max_tool_calls_per_turn"] is not None
    ):
        raise ValueError("Expanded collection must use the frozen protocol")
    return digest(lock)
