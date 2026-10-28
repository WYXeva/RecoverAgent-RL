"""P3-C: autonomous Base-SFT rollouts and immutable, complete-batch checkpoints.

No Teacher, parameter training, state repair, or paid-machine lifecycle operations.
Evaluation and checkpoint metadata are controller-only, never model input.
"""

import argparse
from collections import Counter
import copy
import csv
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse

from .adapter import AppWorldAdapter
from .base_sft import MODEL, REVISION, TEMPLATE_SHA256, load_tokenizer
from .demonstrations import sha256
from .evaluation import environment_check, evaluate_isolated
from .execution_v6 import RUNTIME as PROMPT_RUNTIME, VARIANT as CURRENT_PROMPT
from .harness import REFACTOR_REVISION
from .io import canonical, digest, files_manifest, read_json, write_json
from .sampling import make_plan, official_pool
from .state import capture, inspect_snapshot, restore
from .student import StudentSession, V6_RUNTIME, verify_student_runtime
from .student_evaluation import evaluation_prompt, execution_clock, request_json
from .task_memory import (
    HARNESS_RUNTIME,
    HARNESS_VERSION,
    MODEL_RUNTIME,
    MODEL_RUNTIMES,
    VISIBLE_RUNTIME,
    VISIBLE_VERSION,
    MODEL_VERSION,
    RUNTIME as MEMORY_RUNTIME,
    memory_config,
)

RUNTIME = "p3c_shared_execution_20261026"
BUDGET_STOPS = {"budget_exhausted", "context_budget_exhausted", "completion_claim"}


def verify_source(runtime=RUNTIME):
    if runtime == V6_RUNTIME:
        return verify_student_runtime(runtime)["lock_sha256"]
    root = Path(__file__).resolve().parents[2]
    path = root / (
        "configs/execution_v6_runtime_lock.json"
        if runtime == PROMPT_RUNTIME
        else "configs/egtm_harness_29k_runtime_lock.json"
        if runtime == HARNESS_RUNTIME
        else "configs/egtm_v2_improvement_runtime_lock.json"
        if runtime == VISIBLE_RUNTIME
        else "configs/egtm_v2_runtime_lock.json"
        if runtime == MODEL_RUNTIME
        else "configs/egtm_v1_runtime_lock.json"
        if runtime == MEMORY_RUNTIME
        else "configs/p3c_runtime_20261026_lock.json"
    )
    lock = read_json(path)
    if (
        lock["runtime_revision"] != runtime
        or lock["harness_revision"] != REFACTOR_REVISION
    ):
        raise ValueError("Wrong P3-C runtime lock")
    for name, expected in lock["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Invalid source path")
        if sha256(root / name) != expected:
            raise ValueError("P3-C runtime source changed: " + name)
    return sha256(path)


def verify_checkpoint(directory, release_path, release_sha):
    if sha256(release_path) != release_sha:
        raise ValueError("Checkpoint release reference changed")
    release = read_json(release_path)
    if (release["model"], release["revision"], release["chat_template_sha256"]) != (
        MODEL,
        REVISION,
        TEMPLATE_SHA256,
    ):
        raise ValueError("Wrong formal Student model/tokenizer")
    for name, expected in release["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(Path(directory) / name) != expected
        ):
            raise ValueError("Checkpoint file mismatch: " + name)
    meta = read_json(Path(directory) / "recoveragent_checkpoint.json")
    if (
        not meta["parameter_update_and_reload_pass"]
        or meta["trainable_after_hash"] != release["adapter_tensor_sha256"]
    ):
        raise ValueError("Base-SFT update/reload evidence mismatch")
    return {
        "model": MODEL,
        "revision": REVISION,
        "thinking": False,
        "chat_template_sha256": TEMPLATE_SHA256,
        "checkpoint_id": "adapter:" + release["adapter_tensor_sha256"],
    }


def resource_preflight(output):
    """Read-only evidence from the worker host; never creates paid resources."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        gpus = [
            {
                "name": r[0].strip(),
                "total_mib": int(r[1]),
                "free_mib": int(r[2]),
                "driver": r[3].strip(),
            }
            for r in csv.reader(result.stdout.splitlines())
        ]
    except (OSError, ValueError, subprocess.SubprocessError):
        gpus = []
    return {
        "gpus": gpus,
        "python": sys.version,
        "free_disk_bytes": shutil.disk_usage(output).free,
        "appworld_version": importlib.metadata.version("appworld"),
        "environment": environment_check(),
    }


def fixed_plan(pool, settings, seen_tasks, excluded_tasks, split):
    # Public train membership and dataset membership, never hidden task outcomes.
    eligible = {
        t: s
        for t, s in pool.items()
        if t not in excluded_tasks and split.get(s) == "train"
    }
    # The small official pool has fewer than ten unseen tasks left. Order all
    # eligible tasks by public scenario-balanced sampling, then prefer unseen
    # tasks. Seen-task supplements are explicitly labeled, never held out scores.
    import random

    rng = random.Random(settings["seed"])
    unseen = sorted(set(eligible) - set(seen_tasks))
    rng.shuffle(unseen)
    tasks = unseen[: settings["num_tasks"]]
    seen = sorted(set(eligible) & set(seen_tasks))
    rng.shuffle(seen)
    while len(tasks) < settings["num_tasks"] and seen:
        counts = {
            s: sum(eligible[t] == s for t in tasks) for s in set(eligible.values())
        }
        chosen = min(seen, key=lambda t: counts[eligible[t]])
        tasks.append(chosen)
        seen.remove(chosen)
    explicit = {
        "seed": settings["seed"],
        "strategy": "explicit",
        "num_tasks": settings["num_tasks"],
        "task_ids": tasks,
    }
    plan = make_plan(eligible, explicit)
    plan["settings"] = settings
    for row in plan["rows"]:
        row["exposure"] = (
            "base_sft_seen_task"
            if row["task_id"] in seen_tasks
            else "base_sft_unseen_task"
        )
    plan["selection_inputs"].append("Base-SFT task membership and fixed family split")
    plan["excluded_task_ids"] = sorted(excluded_tasks)
    plan["seen_task_ids"] = sorted(seen_tasks)
    plan["family_split_sha256"] = digest(split)
    plan.pop("plan_id")
    plan["plan_id"] = digest(plan)
    return plan


def validate_config(config):
    if (
        config["runtime_revision"]
        not in {
            PROMPT_RUNTIME,
            V6_RUNTIME,
            RUNTIME,
            MEMORY_RUNTIME,
            HARNESS_RUNTIME,
            *MODEL_RUNTIMES,
        }
        or config["model"] != MODEL
        or config["tool_mode"] != "dynamic_discovery"
        or config["actor_prompt_variant"]
        != (
            CURRENT_PROMPT
            if config["runtime_revision"] in {PROMPT_RUNTIME, V6_RUNTIME}
            else "execution_v5"
        )
        or config["harness_runtime_revision"] != REFACTOR_REVISION
    ):
        raise ValueError("P3-C requires the versioned Base-SFT/v5-or-v6/B protocol")
    memory = memory_config(config.get("memory"))
    if memory["enabled"] and config["runtime_revision"] != (
        HARNESS_RUNTIME
        if memory["version"] == HARNESS_VERSION
        else VISIBLE_RUNTIME
        if memory["version"] == VISIBLE_VERSION
        else MODEL_RUNTIME
        if memory["version"] == MODEL_VERSION
        else MEMORY_RUNTIME
    ):
        raise ValueError("Memory requires its own execution source lock")
    endpoint = urlparse(config["inference_url"])
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in {"127.0.0.1", "localhost"}
        or endpoint.username
    ):
        raise ValueError(
            "Only an already-running private loopback inference backend is supported"
        )
    limits = config["student_limits"]
    if (
        limits["max_new_tokens"] != 4096
        or limits["max_input_tokens"] != 32768
        or not 1 <= limits["max_requests"] <= 40
        or not 1 <= limits["max_generated_tokens"] <= 16384
        or not 0 < limits["wall_seconds"] <= 600
    ):
        raise ValueError("Invalid P3-C collection budget")
    plan = read_json(config["task_manifest"])
    if (
        digest(plan) != config["task_manifest_hash"]
        or not 10 <= len(plan["tasks"]) <= 20
    ):
        raise ValueError("Expected fixed 10–20-task plan")
    from .formal_dataset import json_lines

    dataset = Path(config["dataset"])
    if sha256(dataset / "manifest.json") != config["dataset_manifest_sha256"]:
        raise ValueError("Base-SFT manifest changed")
    seen = {
        s["task_id"]
        for partition in ("train", "validation")
        for s in json_lines(dataset / "model_input" / (partition + ".jsonl"))
    }
    if seen != set(plan["seen_task_ids"]) or read_json(
        config["family_split"]
    ) != read_json(dataset / "control/split.json"):
        raise ValueError("Task exposure or internal validation split changed")
    expected = fixed_plan(
        official_pool(config["appworld_root"]),
        plan["settings"],
        plan["seen_task_ids"],
        plan["excluded_task_ids"],
        read_json(config["family_split"]),
    )
    if expected != plan:
        raise ValueError("Task list cannot be reproduced")
    return plan


def agent_runtime(session):
    """Save budgets/seeds and pending correction state, including numeric value.

    These are control data, not additional Actor-visible messages. P4 must choose
    an explicit continuation policy/budget; it must not silently reset this state.
    """
    guard = copy.deepcopy(vars(session.answer_guard)) if session.answer_guard else None
    if guard and guard["expected"] is not None:
        guard["expected"] = str(guard["expected"])
    return {
        "format": getattr(
            session,
            "runtime_revision",
            session.memory.runtime if session.memory is not None else RUNTIME,
        ),
        "policy": session.identity,
        "tool_mode": session.tool_mode,
        "usage": copy.deepcopy(session.usage),
        "elapsed_seconds": session.clock() - session.started,
        "stopped": session.stopped,
        "termination": session.termination,
        "event_count": len(session.events),
        "generation": {
            k: getattr(session, k) for k in ("seed", "temperature", "top_p")
        },
        "limits": {
            k: getattr(session, k)
            for k in (
                "max_input_tokens",
                "max_new_tokens",
                "max_requests",
                "max_generated_tokens",
                "wall_seconds",
            )
        },
        "harness": copy.deepcopy(vars(session.harness)) if session.harness else None,
        "answer_guard": guard,
        **({"memory": session.memory.identity} if session.memory is not None else {}),
    }


def save_boundary(session, destination, position, provenance):
    task_active = not session.adapter.world.task_completed()
    ref = capture(
        session.adapter,
        destination,
        session.messages,
        session.tools,
        position,
        {
            **provenance,
            "task_active": task_active,
            "resume_eligible": task_active,
            "boundary": "complete_batch",
            "tool_mode": session.tool_mode,
        },
        agent_runtime=agent_runtime(session),
        task_memory=session.memory,
    )
    manifest, _, _ = inspect_snapshot(ref)
    return {
        "snapshot_ref": ref,
        "checkpoint_id": manifest["checkpoint_id"],
        "task_active": task_active,
        "history_hash": manifest["history_hash"],
        "position": position,
    }


def observable_error(event):
    if event.get("protocol_rejection"):
        return "protocol:" + event["protocol_rejection"]
    if any(v for v in event.get("harness_verdicts", [])):
        return "answer_format"
    for result in event.get("tool_results", []):
        text = result["content"]
        if "Execution failed" in text or re.search(
            r"Response status code is [45]\d\d\b", text
        ):
            return "public_tool_error"
    return None


def drive(session, generate, directory, provenance):
    """Checkpoint only complete boundaries. Never repair an interrupted batch."""
    directory = Path(directory)
    boundaries, candidates, fault = [], {}, None
    last_pre, last_post = None, None
    while not session.stopped:
        number = len(session.events) + 1
        try:
            # No model/tool action occurs between two turns. The previous
            # complete post boundary is also the next pre boundary; retain one
            # immutable snapshot and reference its role instead of copying it.
            if last_post is not None:
                last_pre = last_post
            else:
                last_pre = save_boundary(
                    session,
                    directory / "checkpoints" / f"{number:03d}_pre",
                    "pre_action",
                    provenance,
                )
                boundaries.append(last_pre)
        except Exception as exc:
            fault = {
                "category": "worker_environment",
                "phase": "checkpoint",
                "error_type": type(exc).__name__,
            }
            break
        before = len(session.events)
        try:
            event = session.step(generate)
        except Exception as exc:
            if len(session.events) == before and session.termination in BUDGET_STOPS:
                break
            fault = {
                "category": "worker_environment",
                "phase": "execution",
                "error_type": type(exc).__name__,
                "termination": session.termination,
            }
            # Transport/identity failures and partial batches are not model failures.
            break
        try:
            last_post = save_boundary(
                session,
                directory / "checkpoints" / f"{number:03d}_post",
                "post_action",
                provenance,
            )
            boundaries.append(last_post)
        except Exception as exc:
            fault = {
                "category": "worker_environment",
                "phase": "checkpoint",
                "error_type": type(exc).__name__,
            }
            break
        error = observable_error(event)
        # Format re-generation is harness correction, not evidence of an
        # executed business error. Keep its snapshots, but prefer actual public
        # tool errors for post-error recovery candidates.
        if error == "public_tool_error" and "post_error" not in candidates:
            candidates["pre_error"] = {
                **last_pre,
                "error_type": error,
                "interpretation": "prevention_ablation_only",
            }
            candidates["post_error"] = {**last_post, "error_type": error}
    # A completion call may close the task. Use its preceding legal boundary,
    # not a completed-task snapshot, as the late/pre-termination candidate.
    late = last_pre
    if late:
        candidates["late_failure"] = {
            **late,
            "interpretation": "last_active_complete_boundary",
        }
    return {"boundaries": boundaries, "candidates": candidates, "fault": fault}


def classify_episode(evaluation, fault, termination):
    if fault:
        return {
            "outcome": "infrastructure_error",
            "natural_failure": False,
            "reward": None,
        }
    if (
        evaluation.get("status") != "valid"
        or type(evaluation.get("success")) is not bool
    ):
        return {
            "outcome": "evaluation_" + evaluation.get("category", "unknown"),
            "natural_failure": False,
            "reward": None,
        }
    success = evaluation["success"]
    return {
        "outcome": "success" if success else "model_failure",
        "natural_failure": not success,
        "reward": int(success),
        "failure_type": None if success else termination or "official_task_failure",
    }


def run_episode(config, row, directory, provenance, identity):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    adapter, session, fault, collected = (
        None,
        None,
        None,
        {"boundaries": [], "candidates": {}},
    )
    started = execution_clock()
    evaluation = {"status": "evaluation_error", "category": "unknown", "success": None}
    experiment = directory.parent.name + "_" + row["task_id"]
    try:
        # Tokenizer imports happen before the official frozen task clock starts.
        from freezegun import configure

        configure(extend_ignore_list=["transformers", "torch", "huggingface_hub"])
        environment_check()
        tokenizer = load_tokenizer()
        if request_json(config["inference_url"], timeout=15) != identity:
            raise ValueError("Live backend does not match verified Base-SFT checkpoint")
        adapter = AppWorldAdapter(
            config["appworld_root"],
            row["task_id"],
            experiment,
            max_interactions=sys.maxsize,
        )
        session = StudentSession(
            adapter,
            tokenizer,
            [
                {"role": "system", "content": evaluation_prompt(config)},
                {"role": "user", "content": canonical(adapter.public_task())},
            ],
            adapter.initial_tools(),
            tool_mode=config["tool_mode"],
            output=directory / "interaction",
            checkpoint_id=identity["checkpoint_id"],
            answer_guard=True,
            protocol_harness=True,
            harness_revision=REFACTOR_REVISION,
            execution_protocol=(
                V6_RUNTIME if config.get("runtime_revision") == V6_RUNTIME else None
            ),
            clock=execution_clock,
            memory_config=config.get("memory"),
            **config["student_limits"],
        )
        session.runtime_revision = config["runtime_revision"]
        collected = drive(
            session,
            lambda request: request_json(config["inference_url"], request),
            directory,
            provenance,
        )
        fault = collected["fault"]
        # Save before evaluator; incomplete history remains interruption-only.
        adapter.save_terminal()
        adapter.close()
        adapter = None
        try:
            evaluation = evaluate_isolated(
                row["task_id"], experiment, config["appworld_root"], directory
            )
        except Exception as exc:
            evaluation.update(
                category="environment_error", error_type=type(exc).__name__
            )
    except Exception as exc:
        fault = fault or {
            "category": "worker_environment",
            "phase": "initialization_or_terminal",
            "error_type": type(exc).__name__,
        }
    finally:
        if adapter is not None:
            try:
                adapter.close()
            except Exception as exc:
                fault = fault or {
                    "category": "worker_environment",
                    "phase": "cleanup",
                    "error_type": type(exc).__name__,
                }
    termination = session.termination if session else None
    result = {
        "format": config["runtime_revision"],
        **row,
        "split": "train",
        "policy": identity,
        **classify_episode(evaluation, fault, termination),
        "termination": termination,
        "evaluation": evaluation,
        "fault": fault,
        "usage": session.usage if session else {},
        "wall_seconds": execution_clock() - started,
        "boundaries": collected["boundaries"],
        "provenance": provenance,
        "recovery_candidates": collected["candidates"] if not fault else {},
        "harness_corrections_are_recovery_learning": False,
    }
    # No uncertain or successful episode may enter the natural failure index.
    if not result["natural_failure"]:
        result["recovery_candidates"] = {}
    else:
        result["recovery_candidates"] = {
            k: v for k, v in result["recovery_candidates"].items() if v["task_active"]
        }
    write_json(directory / "result.json", result)
    write_json(
        directory / "manifest.json",
        {"files": files_manifest(directory), "policy": identity},
    )
    return result


def restore_probe(root, source, experiment, output):
    """Run in a new process, compare state/history/schema/runtime, execute no task."""
    original, context, runtime = inspect_snapshot(source)
    agent = read_json(Path(source) / "agent_runtime.json")
    adapter = AppWorldAdapter(
        root, original["task_id"], experiment, max_interactions=sys.maxsize
    )
    try:
        manifest, imported = restore(adapter, source)
        public = {t["function"]["name"]: t for t in adapter.catalogue}
        if any(t != public.get(t["function"]["name"]) for t in imported["tools"]):
            raise ValueError("Restored schema not in official public catalogue")
        if adapter.world.task.ground_truth is not None:
            raise ValueError("Actor world unexpectedly loaded hidden ground truth")
        result = {
            "status": "PASS",
            "pid": os.getpid(),
            "checkpoint_id": manifest["checkpoint_id"],
            "database_matches": adapter.fingerprint() == original["logical_state_hash"],
            "history_matches": imported["messages"] == context["messages"],
            "tools_match": imported["tools"] == context["tools"],
            "runtime_matches": adapter.runtime() == runtime,
            "agent_runtime_sha256": digest(agent),
            "agent_runtime_integrity_verified": sha256(
                Path(source) / "agent_runtime.json"
            )
            == original["files"]["agent_runtime.json"],
            "task_active_matches": (not adapter.world.task_completed())
            == original["metadata"]["task_active"],
            "source_unchanged": inspect_snapshot(source)[0] == original,
            "task_actions_executed": 0,
        }
        if original["format_version"] in {3, 4}:
            from .task_memory import TaskMemory

            memory = TaskMemory.load(
                Path(source) / "memory", imported["messages"], imported["tools"]
            )
            result["memory_matches"] = memory.identity == agent["memory"]
            result["memory_identity"] = memory.identity
        if not all(
            v
            for k, v in result.items()
            if k.endswith(("matches", "match", "unchanged"))
        ):
            raise ValueError("Restoration comparison failed")
    finally:
        adapter.close()
    write_json(output, result)
    return result


def collect(config_path, output):
    config = read_json(config_path)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    rows, stop_reason, identity, restorations = [], None, None, []
    try:
        lock = (
            verify_source(config["runtime_revision"])
            if config.get("runtime_revision")
            in {
                PROMPT_RUNTIME,
                V6_RUNTIME,
                MEMORY_RUNTIME,
                HARNESS_RUNTIME,
                *MODEL_RUNTIMES,
            }
            else verify_source()
        )
        plan = validate_config(config)
        identity = verify_checkpoint(
            config["checkpoint"],
            config["checkpoint_release"],
            config["checkpoint_release_sha256"],
        )
        resources = resource_preflight(output)
        write_json(output / "resources.json", resources)
        if (
            not any(
                g["total_mib"] >= config["minimum_gpu_memory_mib"]
                for g in resources["gpus"]
            )
            or resources["free_disk_bytes"] < 10 * 1024**3
        ):
            raise RuntimeError("Required remote GPU capacity/disk unavailable")
        if request_json(config["inference_url"], timeout=15) != identity:
            raise ValueError("Live backend mismatch")
        provenance = {
            "source_lock_sha256": lock,
            "config_sha256": sha256(config_path),
            "plan_id": plan["plan_id"],
            "checkpoint_release_sha256": config["checkpoint_release_sha256"],
        }
        write_json(
            output / "preflight.json",
            {"status": "PASS", "policy": identity, **provenance},
        )
        for row in plan["rows"]:
            directory = output / row["task_id"]
            command = [
                sys.executable,
                "-m",
                "recoveragent.p3c",
                "episode",
                "--config",
                str(config_path),
                "--output",
                str(directory),
                "--task",
                row["task_id"],
            ]
            with (output / (row["task_id"] + ".log")).open("x") as log:
                try:
                    subprocess.run(
                        command,
                        check=True,
                        timeout=config["student_limits"]["wall_seconds"] + 300,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                    result = read_json(directory / "result.json")
                except (subprocess.SubprocessError, OSError, ValueError) as exc:
                    result = {
                        **row,
                        "outcome": "infrastructure_error",
                        "natural_failure": False,
                        "reward": None,
                        "error_type": type(exc).__name__,
                        "usage": {
                            "unknown_generation_reservation": config["student_limits"][
                                "max_generated_tokens"
                            ]
                        },
                        "recovery_candidates": {},
                        "boundaries": [],
                    }
            rows.append(result)
            if result["outcome"] not in {"success", "model_failure"}:
                stop_reason = "worker_or_evaluation_failure; no automatic retry"
                break
            if result["natural_failure"]:
                selected = {
                    v["checkpoint_id"]: v
                    for v in result["recovery_candidates"].values()
                }
                for index, candidate in enumerate(selected.values()):
                    target = output / ("restore_" + row["task_id"] + f"_{index}.json")
                    command = [
                        sys.executable,
                        "-m",
                        "recoveragent.p3c",
                        "restore",
                        "--config",
                        str(config_path),
                        "--source",
                        candidate["snapshot_ref"],
                        "--output",
                        str(target),
                        "--experiment",
                        output.name + "_restore_" + row["task_id"] + f"_{index}",
                    ]
                    with (output / (target.stem + ".log")).open("x") as log:
                        subprocess.run(
                            command,
                            check=True,
                            timeout=180,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                        )
                    restoration = read_json(target)
                    if (
                        restoration["status"] != "PASS"
                        or restoration["checkpoint_id"] != candidate["checkpoint_id"]
                    ):
                        raise ValueError("Independent restore evidence mismatch")
                    restorations.append(restoration)
    except Exception as exc:
        stop_reason = "blocked:" + type(exc).__name__
    # Persist earlier valid failures even when a later worker/restore blocks.
    write_json(
        output / "failures.json",
        [
            {
                k: r[k]
                for k in (
                    "task_id",
                    "scenario_id",
                    "policy",
                    "failure_type",
                    "recovery_candidates",
                )
            }
            for r in rows
            if r["natural_failure"]
        ],
    )
    usage = Counter()
    for row in rows:
        usage.update(row["usage"])
    summary = {
        "format": config.get("runtime_revision", RUNTIME),
        "attempted_tasks": len(rows),
        "natural_failures": sum(r["natural_failure"] for r in rows),
        "successes": sum(r["outcome"] == "success" for r in rows),
        "outcomes": [
            {k: r.get(k) for k in ("task_id", "outcome", "failure_type", "usage")}
            for r in rows
        ],
        "checkpoints": sum(len(r["boundaries"]) for r in rows),
        "independent_restorations": restorations,
        "usage": dict(usage),
        "stop_reason": stop_reason,
        "status": "BLOCKED"
        if stop_reason
        else "PASS"
        if any(r["natural_failure"] for r in rows)
        else "PARTIAL",
        "policy": identity,
        "teacher_or_training_started": False,
    }
    write_json(output / "summary.json", summary)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["collect", "episode", "restore"])
    p.add_argument("--config", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--task")
    p.add_argument("--source")
    p.add_argument("--experiment")
    a = p.parse_args()
    if a.command == "collect":
        print(json.dumps(collect(a.config, a.output)))
    elif a.command == "restore":
        print(
            json.dumps(
                restore_probe(
                    read_json(a.config)["appworld_root"],
                    a.source,
                    a.experiment,
                    a.output,
                )
            )
        )
    else:
        config = read_json(a.config)
        lock = verify_source(config["runtime_revision"])
        plan = validate_config(config)
        row = next(r for r in plan["rows"] if r["task_id"] == a.task)
        identity = verify_checkpoint(
            config["checkpoint"],
            config["checkpoint_release"],
            config["checkpoint_release_sha256"],
        )
        provenance = {
            "source_lock_sha256": lock,
            "config_sha256": sha256(a.config),
            "plan_id": plan["plan_id"],
            "checkpoint_release_sha256": config["checkpoint_release_sha256"],
        }
        r = run_episode(config, row, a.output, provenance, identity)
        print(json.dumps({"task_id": a.task, "outcome": r["outcome"]}))


if __name__ == "__main__":
    main()
