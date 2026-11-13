"""One new source lock for the v2 run; historical locks are never re-signed."""

from pathlib import Path

from .demonstrations import sha256
from .io import read_json
from .v2_context import CONFIG, sequence_limit
from .v2_tasks import ROOT, RUNTIME

LOCK = ROOT / "configs/v2_learning_64k_20261112_lock.json"
EVALUATION_RUNTIME = "v2_base_dev24_envfix_20261113"
S1_PREVIOUS_RUNTIME = "v2_s1_train100_services_20261114"
S1_RUNTIME = "v2_s1_train100_evaluation_20261114"
S1_SNAPSHOT_RUNTIMES = {
    "v2_s1_train100_resume_20261113",
    S1_PREVIOUS_RUNTIME,
    S1_RUNTIME,
}


def verify_lock(runtime=RUNTIME):
    if runtime not in {RUNTIME, EVALUATION_RUNTIME, *S1_SNAPSHOT_RUNTIMES}:
        raise ValueError("Unknown v2 runtime")
    lock_path = (
        ROOT / f"configs/{runtime}_lock.json"
        if runtime in S1_SNAPSHOT_RUNTIMES
        else (
            ROOT / "configs/v2_base_dev24_envfix_20261113_lock.json"
            if runtime == EVALUATION_RUNTIME
            else LOCK
        )
    )
    lock = read_json(lock_path)
    required = {
        str(p.relative_to(ROOT)) for p in (ROOT / "src/recoveragent").glob("*.py")
    }
    required |= {
        str(CONFIG.relative_to(ROOT)),
        "configs/v2_scenario_split_20261109.json",
        "configs/v2_qwen35_weights.json",
        "requirements/v2-learning.txt",
        "scripts/run_v2_round.py",
        "scripts/review_coevolve_pool.py",
        "third_party/agentevolver/LICENSE",
        "third_party/agentevolver/SOURCE.json",
        "third_party/agentevolver/task_abstraction_prompt.txt",
    }
    if runtime == EVALUATION_RUNTIME:
        required |= {
            "configs/v2_base_dev24_envfix_20261113.json",
            "scripts/evaluate_v2_checkpoint.py",
            "scripts/probe_appworld_environment.py",
            "requirements/appworld-evaluation-20261113.lock.txt",
        }
    if runtime in S1_SNAPSHOT_RUNTIMES:
        required |= {
            f"configs/{runtime}.json",
            "configs/v2_s1_train100_20261113.json",
            "configs/v2_s1_train100_20261113_lock.json",
            "scripts/run_v2_s1.py",
            "scripts/probe_v2_vllm.py",
            "scripts/probe_v2_s1_worker.py",
            "configs/teacher_collection_64k_quarantine_20261111.json",
            "configs/v2_base_sft_64k_20261112.json",
            "configs/v2_base_sft_64k_20261112_lock.json",
            "scripts/train_v2_base_64k.py",
            "scripts/probe_appworld_environment.py",
            "requirements/appworld-evaluation-20261113.lock.txt",
        }
        if runtime == S1_RUNTIME:
            required |= {
                f"configs/{S1_PREVIOUS_RUNTIME}.json",
                f"configs/{S1_PREVIOUS_RUNTIME}_lock.json",
            }
    if lock.get("runtime_revision") != runtime or not required <= lock["files"].keys():
        raise ValueError("Incomplete v2 source lock")
    if lock["session_contract"]["budget_caps"]["max_input_tokens"] != sequence_limit():
        raise ValueError("v2 configuration/source lock context mismatch")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("v2 execution source changed: " + name)
    return {
        "revision": runtime,
        "lock_sha256": sha256(lock_path),
        "session_contract": lock["session_contract"],
    }
