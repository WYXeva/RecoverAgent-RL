"""Versioned Actor wrapper; v3/v4 prompt sources remain byte-identical."""

from .answer_guard import RULES
from .execution_v4 import ACTOR_PROMPTS as PREVIOUS_PROMPTS
from .execution_v4 import actor_system as previous_system

ACTOR_PROMPTS = (*PREVIOUS_PROMPTS, "execution_v5")


def actor_system(config):
    if config.get("actor_prompt_variant") != "execution_v5":
        return previous_system(config)
    return (
        previous_system({**config, "actor_prompt_variant": "execution_v4"})
        + "\n"
        + RULES
    )
