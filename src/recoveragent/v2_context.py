"""Shared complete-sequence budget for the current v2 learning pipeline."""

from .io import read_json
from .v2_tasks import ROOT

CONFIG = ROOT / "configs/v2_learning_64k_20261112.json"


def sequence_limit(config=None):
    config = read_json(CONFIG) if config is None else config
    limit = config["student_limits"]["max_input_tokens"]
    if type(limit) is not int or not 0 < limit <= 65536:
        raise ValueError("Invalid v2 complete-sequence limit")
    return limit


def validate_request_context(request, limit):
    """Check the client's declared window against the backend's run budget."""
    if request.get("max_input_tokens") != limit:
        raise ValueError("Worker/backend context budget mismatch")
    reserve = request["max_new_tokens"]
    if (
        type(reserve) is not int
        or not 0 < reserve <= 4096
        or len(request["input_token_ids"]) + reserve > limit
    ):
        raise ValueError("Generation exceeds context; no truncation")
