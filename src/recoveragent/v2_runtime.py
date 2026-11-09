"""One new source lock for the v2 run; historical locks are never re-signed."""

from pathlib import Path

from .demonstrations import sha256
from .io import read_json
from .v2_tasks import ROOT, RUNTIME

LOCK = ROOT / "configs/v2_round1_20261109_lock.json"


def verify_lock():
    lock = read_json(LOCK)
    required = {
        str(p.relative_to(ROOT)) for p in (ROOT / "src/recoveragent").glob("*.py")
    }
    required |= {
        "configs/v2_round1_20261109.json",
        "configs/v2_scenario_split_20261109.json",
        "configs/v2_qwen35_weights.json",
        "requirements/v2-learning.txt",
        "scripts/run_v2_round.py",
    }
    if lock.get("runtime_revision") != RUNTIME or not required <= lock["files"].keys():
        raise ValueError("Incomplete v2 source lock")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("v2 execution source changed: " + name)
    return {
        "revision": RUNTIME,
        "lock_sha256": sha256(LOCK),
        "session_contract": lock["session_contract"],
    }
