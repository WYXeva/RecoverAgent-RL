"""Serial process supervisor, global budgets, and reproducible P0–P2 entry points."""

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

from .buffer import FailureBuffer
from .io import digest, read_json, write_json
from .runner import run_episode
from .state import inspect_snapshot


def worker(config, task_id, directory, seed, source, deadline):
    job_path = directory.parent / (directory.name + ".job.json")
    write_json(
        job_path,
        {
            "config": config,
            "task_id": task_id,
            "run_dir": str(directory),
            "seed": seed,
            "recovery_source": source,
        },
    )
    command = [sys.executable, "-m", "recoveragent.cli", "worker", str(job_path)]
    with (directory.parent / (directory.name + ".log")).open("x") as log:
        try:
            process = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=max(
                    1,
                    min(
                        config["episode_wall_time_seconds"],
                        deadline - time.monotonic(),
                    ),
                ),
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "infrastructure_error",
                "termination_reason": "worker_timeout",
                "reward": None,
                "usage": {
                    "generated_tokens": 0,
                    "generation_reservation_tokens": config["episode_token_allowance"],
                },
                "usage_is_conservative_reservation": True,
            }
    path = directory / "trajectory.json"
    if process.returncode or not path.exists():
        return {
            "status": "infrastructure_error",
            "termination_reason": "worker_crashed",
            "reward": None,
            "usage": {
                "generated_tokens": 0,
                "generation_reservation_tokens": config["episode_token_allowance"],
            },
            "usage_is_conservative_reservation": True,
        }
    return read_json(path)


def group_report(branches, expected_start, source, config):
    valid = [x for x in branches if x["status"] == "valid"]
    rewards = [x["reward"] for x in valid]
    identities = [x.get("start") for x in valid]
    complete = (
        len(branches) == 4
        and len(valid) == 4
        and all(x == expected_start for x in identities)
        and len({x["trajectory_id"] for x in valid}) == 4
        and len({x["seed"] for x in valid}) == 4
        and len({x["worker_pid"] for x in valid}) == 4
    )
    inspect_snapshot(source)
    return {
        "infrastructure_status": "PASS" if complete else "PARTIAL",
        "rewards": rewards,
        "valid_branches": len(valid),
        "expected_branches": 4,
        "same_start": all(x == expected_start for x in identities)
        and len(identities) == 4,
        "source_unchanged": True,
        "recoverability_status": (
            "observed_success"
            if any(rewards)
            else "no_success_observed"
            if rewards
            else "unknown"
        ),
        "statistics_policy": config["policy_identity"],
        "success_at_1": rewards[0] if complete else None,
        "suffix_success_rate": sum(rewards) / len(rewards) if rewards else None,
        "any_success_at_4": int(any(rewards)) if complete else None,
        "reward_variance_available": len(set(rewards)) > 1 if complete else None,
        "grpo_ready": False,
        "grpo_blocker": "Teacher SFT has not been performed",
        "safety": "NOT_MEASURED",
        "branches": branches,
        "group_id": digest(
            {
                "start": expected_start,
                "policy": config["policy_identity"],
                "prompt": config["continuation_prompt"],
                "budget": config["recovery"],
            }
        ),
    }


def validate_config(config):
    if config["training_enabled"] or config["group_size"] != 4:
        raise ValueError("This entry point only supports P0–P2 with four branches")
    if (
        not 1 <= config["max_train_tasks"] <= 20
        or not 1 <= config["total_generated_tokens"] <= 150000
        or not 1 <= config["wall_minutes"] <= 90
        or not 0 <= config["max_infrastructure_retries"] <= 1
    ):
        raise ValueError("First-run hard budget exceeded")
    for mode, turns, tokens in [("normal", 40, 8192), ("recovery", 15, 4096)]:
        budget = config[mode]
        if not (
            1 <= budget["max_model_turns"] <= turns
            and 1 <= budget["max_tool_calls"] <= turns
            and 1 <= budget["max_generated_tokens"] <= tokens
        ):
            raise ValueError("Episode budget exceeds the first-run contract")
    if not (
        1 <= config["policy"]["max_new_tokens"] <= 1024
        and 1 <= config["policy"]["max_input_tokens"] <= 16384
        and 1 <= config["episode_wall_time_seconds"] <= 900
        and 1 <= config["tool_timeout_seconds"] <= 60
        and 1 <= config["model_timeout_seconds"] <= 120
    ):
        raise ValueError("Generation/time limit exceeds the first-run contract")


def run(config, output):
    validate_config(config)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + config["wall_minutes"] * 60
    with urllib.request.urlopen(config["inference_url"] + "/", timeout=10) as response:
        config["policy_identity"] = json.load(response)
    if (
        config["policy_identity"]["model"] != config["model"]
        or config["policy_identity"]["revision"] != config["model_revision"]
    ):
        raise ValueError("Configured Student does not match running inference service")
    write_json(output / "config.json", config)
    from appworld import load_task_ids, update_root

    update_root(str(Path(config["appworld_root"]).resolve()))
    tasks = load_task_ids("train")[: config["max_train_tasks"]]
    write_json(
        output / "task_selection.json",
        {
            "split": "train",
            "task_ids": tasks,
            "rule": "official order; stop at first usable failure",
        },
    )
    buffer = FailureBuffer(output / "failure_buffer")
    attempts, generated, failure = [], 0, None

    def launch(task_id, mode, seed, source=None):
        nonlocal generated
        last = None
        for retry in range(config["max_infrastructure_retries"] + 1):
            allowance = min(
                config[mode]["max_generated_tokens"],
                config["total_generated_tokens"] - generated,
            )
            if allowance <= 0 or time.monotonic() >= deadline:
                break
            local = copy.deepcopy(config)
            local["episode_token_allowance"] = allowance
            directory = output / (mode + "_" + uuid.uuid4().hex[:12])
            last = worker(local, task_id, directory, seed, source, deadline)
            last["evidence_directory"] = str(directory)
            last["infrastructure_retry"] = retry
            generated += (
                last["usage"]["generated_tokens"]
                + last["usage"]["generation_reservation_tokens"]
            )
            attempts.append(last)
            if last["status"] == "valid":
                break
        return last

    for index, task_id in enumerate(tasks):
        result = launch(task_id, "normal", config["seed"] + index * 100)
        if result is None:
            break
        if result["status"] == "valid" and result["reward"] == 0:
            failure = buffer.add(result, result["snapshot_ref"])
            break
        if result["status"] != "valid":
            break  # Investigate repeated infrastructure errors instead of scanning more tasks.
    group = None
    if failure:
        source = failure["snapshot_ref"]
        manifest, _, runtime = inspect_snapshot(source)
        expected = {
            k: manifest[k]
            for k in [
                "checkpoint_id",
                "logical_state_hash",
                "history_hash",
                "tool_schema_hash",
            ]
        }
        expected["runtime_hash"] = digest(runtime)
        branches = []
        # Reserve a full equal recovery allowance for each branch, including retry capacity.
        needed = (
            4
            * config["recovery"]["max_generated_tokens"]
            * (config["max_infrastructure_retries"] + 1)
        )
        if config["total_generated_tokens"] - generated >= needed:
            for i in range(4):
                result = launch(
                    failure["task_id"],
                    "recovery",
                    config["seed"] + 10000 + i * 100,
                    source,
                )
                if result is None:
                    break
                branches.append(result)
        group = group_report(branches, expected, source, config)
        write_json(output / "recovery_group.json", group)
    summary = {
        "status": "PASS"
        if group and group["infrastructure_status"] == "PASS"
        else "PARTIAL",
        "normal_valid": sum(
            x["status"] == "valid" and x.get("mode") == "normal" for x in attempts
        ),
        "natural_failure_collected": failure is not None,
        "generated_tokens": sum(x["usage"]["generated_tokens"] for x in attempts),
        "budget_charged_tokens": generated,
        "wall_seconds": time.monotonic() - started,
        "attempts": len(attempts),
        "infrastructure_errors": sum(x["status"] != "valid" for x in attempts),
        "training_started": False,
    }
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    child = sub.add_parser("worker")
    child.add_argument("job")
    execute = sub.add_parser("run")
    execute.add_argument("--config", default="configs/p0_p2.json")
    execute.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "worker":
        run_episode(**read_json(args.job))
    else:
        run(read_json(args.config), Path(args.output).resolve())


if __name__ == "__main__":
    main()
