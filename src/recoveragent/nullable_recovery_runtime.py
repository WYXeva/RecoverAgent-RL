"""Independent software lock/config for nullable-v2 natural failure collection.

This selects the existing P3-C runner, not a second execution framework. It does
not authorize starting a model, machine, collection or recovery experiment.
"""

from pathlib import Path

from .demonstrations import sha256
from .full732_recovery_tasks import b_tasks
from .io import digest, read_json

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = "p3c_nullable_v2_20261104"
CONFIG = "configs/p3c_nullable_v2_20261104.json"
LOCK = "configs/p3c_nullable_v2_20261104_lock.json"
RELEASE = "configs/p3c_nullable_v2_m1_release_20261104.json"
DEPLOYMENT_KEYS = {"appworld_root", "checkpoint", "inference_url"}


def verify_lock():
    from .student import V6_CONTRACT_SHA256

    path = ROOT / LOCK
    lock = read_json(path)
    required = {
        str(p.relative_to(ROOT)) for p in (ROOT / "src/recoveragent").glob("*.py")
    } | {
        CONFIG,
        RELEASE,
        "configs/unified_task_split_full_pool_candidate.json",
        "configs/student_v6_runtime_lock.json",
        "reports/full732_m1_checkpoint_release_20261030.json",
        "requirements/appworld-harness.lock.txt",
        "scripts/continue_p3c.py",
    }
    if (
        lock.get("runtime_revision") != RUNTIME
        or digest(lock.get("session_contract")) != V6_CONTRACT_SHA256
        or not required <= lock.get("files", {}).keys()
    ):
        raise ValueError("Incomplete nullable recovery source lock/contract")
    for name, expected in lock["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Invalid nullable recovery source path")
        if sha256(ROOT / name) != expected:
            raise ValueError("Nullable recovery runtime source changed: " + name)
    return {
        "revision": RUNTIME,
        "lock_sha256": sha256(path),
        "session_contract": lock["session_contract"],
    }


def validate_config(config):
    verify_lock()
    expected = read_json(ROOT / CONFIG)
    if {k: v for k, v in config.items() if k not in DEPLOYMENT_KEYS} != {
        k: v for k, v in expected.items() if k not in DEPLOYMENT_KEYS
    }:
        raise ValueError("Nullable recovery configuration differs from frozen contract")
    plan, rows = b_tasks()
    result = {"tasks": [r["task_id"] for r in rows], "rows": rows}
    result["plan_id"] = digest(
        {"split_manifest_sha256": plan["manifest_sha256"], **result}
    )
    return result
