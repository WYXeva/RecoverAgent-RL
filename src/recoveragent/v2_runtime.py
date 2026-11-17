"""One new source lock for the v2 run; historical locks are never re-signed."""

import os
from pathlib import Path

from .demonstrations import sha256
from .io import read_json
from .v2_context import CONFIG, sequence_limit
from .v2_tasks import ROOT, RUNTIME

LOCK = ROOT / "configs/v2_learning_64k_20261112_lock.json"
EVALUATION_RUNTIME = "v2_base_dev24_envfix_20261113"
S1_PREVIOUS_RUNTIME = "v2_s1_train100_services_20261114"
S1_RUNTIME = "v2_s1_train100_evaluation_20261114"
S1_RETRAIN_RUNTIME = "v2_s1_retrain_20261114"
S1_SHORT_RUNTIME = "v2_s1_short_20261115"
S1_SHORT_VLLM_RUNTIME = "v2_s1_short_vllm_20261115"
S2_RUNTIME = "v2_s2_static_evolve_20261116"
S2_PARALLEL_EXECUTION = "v2_s2_parallel_20261116"
S2_GENERATION_EXECUTION = "v2_s2_generation_20261116"
S2_BACKGROUND_EXECUTION = "v2_s2_background_20261117"
S2_ADMISSION_EXECUTION = "v2_s2_admission_20261117"
S2_REVIEW_EXECUTION = "v2_s2_review_20261117"
S2_SUPERVISED_EXECUTION = "v2_s2_supervised_20261117"
S2_NATIVE_EXECUTION = "v2_s2_native_nullable_20261117"
S2_GOAL_EXECUTION = "v2_s2_goal_reward_20261117"
S2_GOAL_REPAIR_EXECUTION = "v2_s2_goal_repair_20261117"
S2_LIGHT_EXECUTION = "v2_s2_light_llm_20261117"
S2_TEACHER_CONTEXT_EXECUTION = "v2_s2_teacher_context_20261117"
S2_JUDGE_THINKING_EXECUTION = "v2_s2_judge_thinking_20261117"
S2_EVOLVE_ROUND1_EXECUTION = "v2_s2_evolve_round1_20261117"


def s2_execution():
    revision = os.environ.get("RECOVERAGENT_S2_EXECUTION")
    if revision is None:
        return {"revision": "original_serial", "rollout_concurrency": 1}
    if revision not in {
        S2_PARALLEL_EXECUTION,
        S2_GENERATION_EXECUTION,
        S2_BACKGROUND_EXECUTION,
        S2_ADMISSION_EXECUTION,
        S2_REVIEW_EXECUTION,
        S2_SUPERVISED_EXECUTION,
        S2_NATIVE_EXECUTION,
        S2_GOAL_EXECUTION,
        S2_GOAL_REPAIR_EXECUTION,
        S2_LIGHT_EXECUTION,
        S2_TEACHER_CONTEXT_EXECUTION,
        S2_JUDGE_THINKING_EXECUTION,
        S2_EVOLVE_ROUND1_EXECUTION,
    }:
        raise ValueError("Unknown S2 execution revision")
    return read_json(ROOT / f"configs/{revision}.json")


def s2_group_limit():
    rounds = s2_execution().get("stop_after_round", 5)
    if type(rounds) is not int or not 1 <= rounds <= 5:
        raise ValueError("S2 stop_after_round must be between one and five")
    return rounds * 40


S1_SNAPSHOT_RUNTIMES = {
    "v2_s1_train100_resume_20261113",
    S1_PREVIOUS_RUNTIME,
    S1_RUNTIME,
    S1_RETRAIN_RUNTIME,
    S1_SHORT_RUNTIME,
    S1_SHORT_VLLM_RUNTIME,
    S2_RUNTIME,
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
    execution = s2_execution() if runtime == S2_RUNTIME else None
    if execution and execution["revision"] != "original_serial":
        lock_path = ROOT / f"configs/{execution['revision']}_lock.json"
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
        if runtime in {S1_RETRAIN_RUNTIME, S1_SHORT_RUNTIME, S1_SHORT_VLLM_RUNTIME}:
            required |= {
                "scripts/retrain_v2_s1.py",
                f"configs/{S1_RUNTIME}.json",
                f"configs/{S1_RUNTIME}_lock.json",
                "configs/teacher_escalation_local_20261114.json",
            }
        if runtime == S1_SHORT_RUNTIME:
            required.add("scripts/train_v2_s1_short.py")
        if runtime == S1_SHORT_VLLM_RUNTIME:
            required |= {
                f"configs/{S1_SHORT_RUNTIME}.json",
                f"configs/{S1_SHORT_RUNTIME}_lock.json",
                f"configs/{S1_SHORT_RUNTIME}_plan.json",
                "scripts/evaluate_v2_s1_short_vllm.py",
                "requirements/v2-vllm-20261115.txt",
                "requirements/v2-vllm-20261115.lock.txt",
            }
        if runtime == S2_RUNTIME:
            required |= {
                "scripts/run_v2_s2.py",
                "configs/v2_s2_inputs_20261116.json",
                "configs/v2_s1_short_vllm_20261115.json",
                "requirements/v2-vllm-20261115.lock.txt",
            }
            if execution["revision"] != "original_serial":
                required |= {
                    f"configs/{execution['revision']}.json",
                    f"configs/{S2_RUNTIME}_lock.json",
                }
                if execution["revision"] in {
                    S2_GOAL_EXECUTION,
                    S2_GOAL_REPAIR_EXECUTION,
                }:
                    required |= {
                        "scripts/probe_s2_reward_chain.py",
                        "scripts/audit_s2_reward_chain.py",
                        "configs/s2_reward_fresh16_20261117.json",
                        f"configs/{S2_NATIVE_EXECUTION}_lock.json",
                    }
                if execution["revision"] == S2_GOAL_REPAIR_EXECUTION:
                    required |= {
                        "configs/s2_reward_fresh16_repair_20261117.json",
                        f"configs/{S2_GOAL_EXECUTION}_lock.json",
                    }
                if execution["revision"] == S2_LIGHT_EXECUTION:
                    required |= {
                        "configs/s2_llm_fresh16_20261117.json",
                        "configs/s2_llm_judge_check_20261117.json",
                        f"configs/{S2_GOAL_REPAIR_EXECUTION}_lock.json",
                        "scripts/probe_s2_reward_chain.py",
                    }
                if execution["revision"] == S2_TEACHER_CONTEXT_EXECUTION:
                    required |= {
                        f"configs/{S2_LIGHT_EXECUTION}_lock.json",
                        "configs/s2_llm_judge_check_20261117.json",
                        "scripts/probe_s2_coevolve.py",
                    }
                if execution["revision"] == S2_JUDGE_THINKING_EXECUTION:
                    required |= {
                        f"configs/{S2_TEACHER_CONTEXT_EXECUTION}_lock.json",
                        "configs/s2_judge_thinking_check_20261117.json",
                        "scripts/recheck_s2_judge.py",
                    }
                if execution["revision"] == S2_EVOLVE_ROUND1_EXECUTION:
                    required |= {
                        f"configs/{S2_JUDGE_THINKING_EXECUTION}_lock.json",
                        "scripts/supervise_s2_background.py",
                    }
                if (
                    lock.get("execution_revision") != execution["revision"]
                    or execution["protocol_runtime"] != runtime
                    or execution["rollout_concurrency"] != 2
                    or execution["evaluation_concurrency"] != 1
                ):
                    raise ValueError("S2 execution settings changed")
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
    result = {
        "revision": runtime,
        "lock_sha256": sha256(lock_path),
        "session_contract": lock["session_contract"],
    }
    if execution and execution["revision"] != "original_serial":
        result["execution_revision"] = execution["revision"]
    return result
