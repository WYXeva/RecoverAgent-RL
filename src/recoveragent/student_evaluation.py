"""Frozen, paired Raw/Base-SFT evaluation. No evaluation data enter training."""

import argparse
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import urllib.request

from . import APPWORLD_REVISION
from .adapter import AppWorldAdapter
from .base_sft import load_tokenizer
from .evaluation import environment_check, evaluate_here
from .execution_v5 import actor_system
from .harness import REFACTOR_REVISION, RULES
from .io import digest, files_manifest, read_json, write_json
from .student import StudentSession
from .task_memory import MODEL_RUNTIME, MODEL_VERSION, RUNTIME as MEMORY_RUNTIME

RUNTIME = "p3b_shared_execution_20261026"


def execution_clock():
    # Official AppWorld freezes time.monotonic, but not this Linux system clock.
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def verify_execution_runtime(runtime=RUNTIME):
    root = Path(__file__).resolve().parents[2]
    path = root / (
        "configs/egtm_v2_runtime_lock.json"
        if runtime == MODEL_RUNTIME
        else "configs/egtm_v1_runtime_lock.json"
        if runtime == MEMORY_RUNTIME
        else "configs/p3b_evaluation_runtime_20261026_lock.json"
    )
    lock = read_json(path)
    if (
        lock["runtime_revision"] != runtime
        or lock["harness_revision"] != REFACTOR_REVISION
    ):
        raise ValueError("Wrong evaluation runtime lock")
    for name, expected in lock["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Invalid evaluation source path")
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Evaluation runtime source changed: " + name)
    return {
        "revision": lock["runtime_revision"],
        "lock_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def evaluation_prompt(config):
    limits = config["student_limits"]
    return (
        actor_system(
            {
                "actor_prompt_variant": config["actor_prompt_variant"],
                "tool_mode": config["tool_mode"],
                "limits": {
                    "max_tool_calls": None,
                    "episode_generated_tokens": limits["max_generated_tokens"],
                    "episode_wall_seconds": limits["wall_seconds"],
                },
                "teacher": {
                    "max_tool_calls_per_turn": None,
                    "max_new_tokens": limits["max_new_tokens"],
                },
                "remaining_generated_tokens": limits["max_generated_tokens"],
                "remaining_requests": limits["max_requests"],
            }
        )
        + "\n"
        + RULES
    )


class EvaluationAdapter(AppWorldAdapter):
    """Same public Unified calls; explicitly permits official dev for evaluation.

    Keep the train-only P0/P2 adapter and its historical source lock unchanged.
    Inherited execution/state methods are identical; no hidden tools registered.
    """

    def __init__(self, root, task_id, experiment_name, split):
        from appworld import AppWorld, load_task_ids, update_root
        from appworld.common.path_store import path_store

        if split not in {"train", "dev"}:
            raise ValueError("Only explicit train/dev evaluation is allowed")
        update_root(str(Path(root).resolve()))
        if task_id not in load_task_ids(split):
            raise ValueError("Task does not belong to declared evaluation split")
        direct = importlib.metadata.distribution("appworld").read_text(
            "direct_url.json"
        )
        if not direct or APPWORLD_REVISION not in direct:
            raise ValueError("Pinned official AppWorld source required")
        output = (
            Path(path_store.experiment_outputs) / experiment_name / "tasks" / task_id
        )
        if output.exists():
            raise FileExistsError("Refusing to reuse environment output")
        self.task_id, self.experiment_name = task_id, experiment_name
        self.world = AppWorld(
            task_id=task_id,
            experiment_name=experiment_name,
            load_ground_truth=False,
            include_direct_functions=True,
            direct_function_separator="__",
            random_seed=42,
            max_interactions=sys.maxsize,
            max_api_calls_per_interaction=1,
            timeout_seconds=60,
            allow_datetime_change=False,
            add_login_shortcut=False,
        )
        self.catalogue = self.world.task.api_docs.function_calling()
        if any(t["function"]["name"].startswith("admin__") for t in self.catalogue):
            raise ValueError("Admin tools must not be exposed")
        self.versions = {
            "appworld": importlib.metadata.version("appworld"),
            "revision": APPWORLD_REVISION,
            "data_version": self.world.task.db_version,
        }


def request_json(url, payload=None, timeout=180):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def evaluate_terminal(task, experiment, root, split, output):
    destination = Path(output) / "official_evaluation.json"
    with (Path(output) / "evaluation_worker.log").open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "recoveragent.student_evaluation",
                "official",
                "--task",
                task,
                "--experiment",
                experiment,
                "--root",
                root,
                "--split",
                split,
                "--output",
                str(destination),
            ],
            check=True,
            timeout=120,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    return read_json(destination)


def run_episode(config, case, arm, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = execution_clock()
    adapter, session = None, None
    experiment = "p3b_eval_" + digest(str(output.resolve()))[:24]
    record = {
        "task_id": case["task_id"],
        "partition": case["partition"],
        "split": case["split"],
        "arm": arm,
        "status": "infrastructure_error",
        "official_success": None,
        "training_started": False,
        "scope": "p3b_fixed_sample_evaluation",
        "config_hash": digest(config),
        "experiment": experiment,
    }
    try:
        record["evaluation_runtime"] = (
            verify_execution_runtime(config["runtime_revision"])
            if config.get("runtime_revision") in {MEMORY_RUNTIME, MODEL_RUNTIME}
            else verify_execution_runtime()
        )
        if config.get("memory", {}).get("enabled") and config.get(
            "runtime_revision"
        ) != (
            MODEL_RUNTIME
            if config["memory"].get("version") == MODEL_VERSION
            else MEMORY_RUNTIME
        ):
            raise ValueError("Memory requires its own execution source lock")
        if config["tool_mode"] != "dynamic_discovery":
            raise ValueError("This frozen evaluation uses dynamic discovery")
        from freezegun import configure

        # Freezegun scans newly imported lazy modules on teardown. Keep tokenizer
        # internals outside its patching; all AppWorld/task clocks remain frozen.
        configure(extend_ignore_list=["transformers"])
        tokenizer = load_tokenizer()
        record["environment"] = environment_check()
        identity = request_json(config["inference_url"])
        if (
            config.get("expected_policy") is not None
            and identity != config["expected_policy"]
        ):
            raise ValueError(
                "Serving checkpoint differs from the fixed experiment policy"
            )
        adapter = EvaluationAdapter(
            config["appworld_root"], case["task_id"], experiment, case["split"]
        )
        record["initial_state_hash"] = adapter.fingerprint()
        record["scenario_id"] = adapter.world.task.generator_id
        if record["scenario_id"] != case["scenario_id"]:
            raise ValueError("Official scenario differs from the committed plan")
        messages = [
            {"role": "system", "content": evaluation_prompt(config)},
            {
                "role": "user",
                "content": json.dumps(adapter.public_task(), ensure_ascii=False),
            },
        ]
        session = StudentSession(
            adapter,
            tokenizer,
            messages,
            adapter.initial_tools(),
            tool_mode="dynamic_discovery",
            output=output / "session",
            checkpoint_id=identity["checkpoint_id"],
            answer_guard=True,
            protocol_harness=True,
            harness_revision=REFACTOR_REVISION,
            clock=execution_clock,
            memory_config=config.get("memory"),
            **config["student_limits"],
        )
        if identity != session.identity:
            raise ValueError("Serving policy identity does not match evaluation arm")
        if (arm == "raw_base") != identity["checkpoint_id"].startswith("raw_base:"):
            raise ValueError("Requested evaluation arm is not loaded")
        record["policy"] = identity
        result = session.run(lambda req: request_json(config["inference_url"], req))
        record["session_result"] = result
        adapter.export_state("p3b_eval_terminal", output / "terminal/dbs")
        write_json(
            output / "terminal/context.json",
            {"messages": session.messages, "tools": session.tools},
        )
        write_json(output / "terminal/runtime.json", adapter.runtime())
        if session.memory is not None:
            session.memory.save(
                output / "terminal/memory", session.messages, session.tools
            )
        write_json(
            output / "terminal/manifest.json",
            {
                "format": "p3b-evaluation-terminal-v1",
                "split": case["split"],
                "history_hash": digest(session.messages),
                "logical_state_hash": adapter.fingerprint(),
                "files": files_manifest(output / "terminal"),
                "resume_eligible": False,
            },
        )
        adapter.save_terminal()
        closing, adapter = adapter, None
        closing.close()
        evaluation = evaluate_terminal(
            case["task_id"], experiment, config["appworld_root"], case["split"], output
        )
        record.update(
            status=evaluation["status"],
            official_success=evaluation["success"],
            evaluation_exception_types=evaluation["exception_types"],
        )
    except Exception as exc:
        record["error_type"] = type(exc).__name__
        (output / "exception.log").write_text(traceback.format_exc())
    finally:
        if session:
            record.update(
                usage=session.usage,
                termination=session.termination,
                protocol_rejections=session.harness.rejections,
                answer_guard=session.answer_guard.summary(),
            )
        if adapter:
            try:
                adapter.close()
            except Exception as exc:
                record["cleanup_error_type"] = type(exc).__name__
        record["wall_seconds"] = execution_clock() - started
        write_json(output / "result.json", record)
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["worker", "official", "collect"])
    p.add_argument("--config")
    p.add_argument("--output", required=True)
    p.add_argument("--task")
    p.add_argument("--experiment")
    p.add_argument("--root")
    p.add_argument("--split", choices=["train", "dev"])
    p.add_argument("--arm", choices=["raw_base", "base_sft"])
    p.add_argument("--case-index", type=int)
    a = p.parse_args()
    if a.command == "official":
        from appworld import update_root, load_task_ids

        environment_check()
        update_root(str(Path(a.root).resolve()))
        if a.task not in load_task_ids(a.split):
            raise ValueError("Wrong official task split")
        write_json(a.output, evaluate_here(a.task, a.experiment))
        return
    config = read_json(a.config)
    if a.command == "worker":
        result = run_episode(config, config["cases"][a.case_index], a.arm, a.output)
        print(
            json.dumps(
                {k: result[k] for k in ["task_id", "status", "official_success"]}
            )
        )
        return
    output = Path(a.output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config.json", config)
    rows = []
    for index, case in enumerate(config["cases"]):
        target = output / (a.arm + "_" + case["partition"] + "_" + case["task_id"])
        try:
            with (output / (target.name + ".log")).open("x") as log:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "recoveragent.student_evaluation",
                        "worker",
                        "--config",
                        str(Path(a.config).resolve()),
                        "--case-index",
                        str(index),
                        "--arm",
                        a.arm,
                        "--output",
                        str(target.resolve()),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=config["student_limits"]["wall_seconds"] + 180,
                    check=True,
                    env=dict(os.environ),
                )
            row = read_json(target / "result.json")
        except (subprocess.SubprocessError, OSError) as exc:
            row = {
                **case,
                "arm": a.arm,
                "status": "infrastructure_error",
                "official_success": None,
                "error_type": type(exc).__name__,
                "usage_unknown": True,
                "terminal_state_not_guaranteed": True,
            }
            write_json(output / (target.name + ".worker_error.json"), row)
        rows.append(row)
        print(
            json.dumps(
                {
                    "task": case["task_id"],
                    "partition": case["partition"],
                    "status": row["status"],
                    "success": row["official_success"],
                }
            ),
            flush=True,
        )
        # Official non-assertion failures can depend on an incomplete task state.
        # Preserve an unknown outcome, and keep the precommitted evaluation list.
        # A worker/backend failure still stops the arm to avoid repeated outages.
        if row["status"] == "infrastructure_error":
            break
    write_json(
        output / "summary.json",
        {
            "arm": a.arm,
            "attempts": len(rows),
            "rows": rows,
            "unattempted": config["cases"][len(rows) :],
            "training_started": False,
        },
    )


if __name__ == "__main__":
    main()
