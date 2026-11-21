"""Frozen test111 comparison, rolling four environments with task-local 2-way retry."""

import argparse
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
import fcntl
import multiprocessing
import os
from pathlib import Path
import time

from recoveragent.io import digest, read_json, write_json
from recoveragent.v2_tasks import CODEACT_TEST, context_for, plan
from recoveragent.v2_training import verify_checkpoint

from .collect import replace_json
from .episode import run
from .experiment import Server, process_identity, verify_source
from .inference import VLLMStudent, evaluation_tasks
from .progress import execution_identity
from .protocol import identity
from .rubric import REVISION as RUBRIC, diagnostics, totals
from .sampling import resource_error, retryable

REVISION = "codeact_paired_test111_v1"


def attempt_binding(config, model, task):
    return {
        "revision": REVISION,
        "task_id": task,
        "model": model,
        "seed": config["seed"],
        "config_sha256": digest(config),
        "protocol": identity(),
        "student_execution": execution_identity(config),
    }


def recover_usage(directory):
    """Read actual receipts only; count unanswered requests separately."""
    tokens = [read_json(p) for p in (directory / "requests").glob("*.tokens.json")]
    requests = len(list((directory / "requests").glob("*.backend_request.json")))
    return {
        "requests": requests,
        "prompt_tokens": sum(len(r["input_ids"]) for r in tokens),
        "generated_tokens": sum(len(r["generated_ids"]) for r in tokens),
        "seconds": sum(r["seconds"] for r in tokens),
        "unknown_requests": max(0, requests - len(tokens)),
    }


def recover_attempt(config, model, task, directory, reason):
    directory = Path(directory)
    target = directory / "evaluation_row.json"
    if target.exists():
        row = read_json(target)
        if row["binding"] != attempt_binding(config, model, task):
            raise ValueError("Recovered evaluation identity changed")
        return row
    episode = directory / "episode"
    if (episode / "result.json").exists():
        return finish_attempt(config, model, task, directory)
    row = {
        "binding": attempt_binding(config, model, task),
        "task_id": task,
        "directory": str(directory),
        "success": None,
        "reward_valid": False,
        "failure_stage": "sampling",
        "failure_type": "SamplingCapacityError",
        "fault": reason,
        "termination": "interrupted_evaluation",
        "usage": recover_usage(episode),
        "usage_unknown": True,
        "rubric": diagnostics(episode, {}),
        "initial_state_hash": None,
    }
    write_json(target, row)
    return row


def finish_attempt(config, model, task, directory):
    directory = Path(directory)
    episode = directory / "episode"
    result, trajectory = (
        read_json(episode / "result.json"),
        read_json(episode / "trajectory.json"),
    )
    if (
        trajectory["task_id"] != task
        or trajectory["config_sha256"] != digest(config)
        or trajectory["custom_research"] != context_for(task, final_test=CODEACT_TEST)
        or trajectory["student_execution"] != execution_identity(config)
    ):
        raise ValueError("Completed evaluation provenance changed")
    row = {
        "binding": attempt_binding(config, model, task),
        "task_id": task,
        "directory": str(directory),
        "success": result["success"],
        "reward_valid": result["valid"],
        "failure_stage": "sampling" if trajectory.get("fault") else "evaluation",
        "fault": trajectory.get("fault") or result.get("evaluation", {}).get("error"),
        "termination": result["termination"],
        "usage": result["usage"],
        "seconds": result["seconds"],
        "trajectory_sha256": result["trajectory_sha256"],
        "result_sha256": digest(result),
        "initial_state_hash": trajectory["initial_state_hash"],
        "rubric": diagnostics(episode, result),
    }
    write_json(directory / "evaluation_row.json", row)
    return row


def evaluate_task(config, deployment, model, request, directory):
    """One fresh child per task, native loop and original official scoring."""
    directory = Path(directory)
    task = request["task_id"]
    context_for(task, final_test=CODEACT_TEST)
    checkpoint = verify_checkpoint(request["actor"], config["model"])
    if checkpoint["adapter_sha256_after"] != model["adapter_sha256_after"]:
        raise ValueError("Evaluation checkpoint is not the frozen model")
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "evaluation_row.json").exists():
        return recover_attempt(config, model, task, directory, "cached")
    if (directory / "episode").exists():
        return recover_attempt(
            config, model, task, directory, "Interrupted attempt retained"
        )
    write_json(
        directory / "worker.json",
        {"pid": os.getpid(), "identity": process_identity(os.getpid())},
    )
    started = time.time()
    try:
        client = VLLMStudent(
            config,
            request["actor"],
            request["server"]["url"],
            request["server_receipt"],
        )
        run(
            task,
            request["attempt"],
            directory / "episode",
            deployment["appworld_root"],
            config,
            client,
            training=False,
            final_test=CODEACT_TEST,
        )
        return finish_attempt(config, model, task, directory)
    except Exception as exc:
        # Ordinary model errors are already handled by the agent loop. Fail closed
        # for environment/client errors; only capacity/service failures may retry.
        row = {
            "binding": attempt_binding(config, model, task),
            "task_id": task,
            "directory": str(directory),
            "success": None,
            "reward_valid": False,
            "failure_stage": "sampling",
            "failure_type": type(exc).__name__,
            "fault": str(exc),
            "termination": "worker_error",
            "seconds": time.time() - started,
            "usage": recover_usage(directory / "episode"),
            "usage_unknown": True,
            "initial_state_hash": None,
            "rubric": diagnostics(directory / "episode", {}),
        }
        write_json(directory / "evaluation_row.json", row)
        return row


def rolling(tasks, width, submit, completed, *, executor_factory=None):
    """Refill a free slot immediately; stop refilling after a capacity fault."""
    remaining = deque(tasks)
    factory = executor_factory or (
        lambda: ProcessPoolExecutor(
            max_workers=width,
            mp_context=multiprocessing.get_context("spawn"),
            max_tasks_per_child=1,
        )
    )
    stopped = False
    with factory() as pool:
        active = {}

        def fill():
            while remaining and len(active) < width and not stopped:
                task = remaining.popleft()
                active[submit(pool, task)] = task

        fill()
        while active:
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                task = active.pop(future)
                try:
                    row = future.result()
                except (BrokenProcessPool, MemoryError, OSError) as exc:
                    if isinstance(exc, OSError) and not resource_error(exc):
                        raise
                    row = exc
                fault = completed(task, row)
                stopped = stopped or (width == 4 and fault)
            fill()
    return list(remaining)


def selected_rows(directory, tasks):
    rows, discarded = [], []
    for task in tasks:
        path = Path(directory) / "tasks" / task / "selected.json"
        if not path.exists():
            continue
        selected = read_json(path)
        row = read_json(selected["row_path"])
        if digest(row) != selected["row_sha256"] or row["task_id"] != task:
            raise ValueError("Selected test result changed")
        rows.append(row)
        if selected["attempt"] == 2:
            discarded.append(read_json(path.parent / "attempt_1/evaluation_row.json"))
    return rows, discarded


def model_summary(directory, tasks, status):
    rows, discarded = selected_rows(directory, tasks)
    value = {
        "status": status,
        **totals(rows),
        "attempts": len(rows) + len(discarded),
        "capacity_retries": len(discarded),
        "discarded_attempt_usage": totals(discarded)["usage"],
        "all_attempt_usage": totals(rows + discarded)["usage"],
        "discarded_attempt_rubric": totals(discarded)["rubric_totals"],
    }
    replace_json(Path(directory) / "summary.json", value)
    return value


def evaluate_model(config, deployment, model, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tasks = config["evaluation"]["tasks"]
    actor = deployment["models"][model["name"]]
    server = None
    primary_only = config["evaluation"].get("primary_attempt_only", False)

    def ensure_server(width):
        nonlocal server
        if server is not None and (
            server.process.poll() is not None or server.proof["max_num_seqs"] != width
        ):
            server.close()
            server = None
        if server is None:
            server = Server(
                config,
                deployment,
                actor,
                directory / "servers" / str(time.time_ns()),
                concurrency=width,
            )
        return server

    def select(task, attempt, row):
        write_json(
            directory / "tasks" / task / "selected.json",
            {
                "attempt": attempt,
                "row_path": str(Path(row["directory"]) / "evaluation_row.json"),
                "row_sha256": digest(row),
            },
        )
        model_summary(directory, tasks, "running")

    def phase(phase_tasks, width, attempt):
        nonlocal server
        if not phase_tasks:
            return []
        ensure_server(width)

        def submit(pool, task):
            path = directory / "tasks" / task / f"attempt_{attempt}"
            write_json(
                path / "intent.json",
                {
                    "binding": attempt_binding(config, model, task),
                    "attempt": attempt,
                    "concurrency": width,
                },
            )
            req = {
                "task_id": task,
                "attempt": attempt,
                "actor": actor,
                "server": server.proof,
                "server_receipt": str(server.receipt),
            }
            return pool.submit(evaluate_task, config, deployment, model, req, str(path))

        def completed(task, row):
            path = directory / "tasks" / task / f"attempt_{attempt}"
            if isinstance(row, BaseException):
                row = recover_attempt(config, model, task, path, str(row))
            if row["binding"] != attempt_binding(config, model, task):
                raise ValueError("Worker returned a different evaluation identity")
            should_retry = retryable(row)
            if primary_only or attempt == 2 or not should_retry:
                select(task, attempt, row)
            return should_retry

        return rolling(phase_tasks, width, submit, completed)

    try:
        # No second invocation can race these workers: the suite holds flock.
        for task in tasks:
            target = directory / "tasks" / task
            if (target / "selected.json").exists():
                selected_rows(directory, [task])
                continue
            for attempt in (1, 2):
                path = target / f"attempt_{attempt}"
                if not (path / "intent.json").exists():
                    continue
                if read_json(path / "intent.json")["binding"] != attempt_binding(
                    config, model, task
                ):
                    raise ValueError("Interrupted test attempt identity changed")
                worker_path = path / "worker.json"
                if worker_path.exists():
                    worker = read_json(worker_path)
                    while (
                        process_identity(worker["pid"]) == worker["identity"]
                        and not (path / "evaluation_row.json").exists()
                    ):
                        time.sleep(2)
                row = recover_attempt(
                    config,
                    model,
                    task,
                    path,
                    "Interrupted evaluation process; original attempt retained",
                )
                if primary_only or attempt == 2 or not retryable(row):
                    select(task, attempt, row)
        while True:
            pending = [
                task
                for task in tasks
                if not (directory / "tasks" / task / "selected.json").exists()
            ]
            if not pending:
                return model_summary(directory, tasks, "complete")
            if primary_only:
                remaining = phase(pending, 4, 1)
                if remaining:
                    # Capacity failure stays Unknown. Reduce concurrency for the
                    # next two unstarted tasks, then return to four; never replay.
                    phase(remaining[:2], 2, 1)
                continue
            retries = [
                task
                for task in pending
                if (directory / "tasks" / task / "attempt_1/intent.json").exists()
            ]
            if retries:
                # Recover the backend too, not just the AppWorld child count.
                if server is not None:
                    server.close()
                    server = None
                phase(retries, 2, 2)
            else:
                phase(pending, 4, 1)
    finally:
        if server is not None:
            server.close()


def comparison(directory, config):
    tasks = config["evaluation"]["tasks"]
    by_model = {}
    for model in config["evaluation"]["models"]:
        rows, _ = selected_rows(Path(directory) / model["name"], tasks)
        by_model[model["name"]] = {r["task_id"]: r for r in rows}
    pairs = []
    for task in tasks:
        records = {name: rows[task] for name, rows in by_model.items() if task in rows}
        hashes = {
            r["initial_state_hash"]
            for r in records.values()
            if r["initial_state_hash"] is not None
        }
        if len(hashes) > 1:
            raise ValueError("Same-task initial environment differs across checkpoints")
        pairs.append(
            {
                "task_id": task,
                "outcomes": {name: r["success"] for name, r in records.items()},
                "initial_state_hash": next(iter(hashes), None),
            }
        )
    replace_json(
        Path(directory) / "comparison.json", {"revision": REVISION, "tasks": pairs}
    )


def import_primary_attempts(config, deployment, output):
    """Carry immutable primary attempts into a stricter reporting scope; no replay.

    The actual old configuration/binding stays in the origin receipt. Only runs
    with identical model-visible configuration, task order and model identities
    may be carried forward. Diagnostic retry attempts are explicitly excluded.
    """
    origin = deployment.get("import_primary_run")
    if not origin:
        return
    origin = Path(origin)
    original_binding = read_json(origin / "binding.json")
    original = original_binding["config"]

    def execution(cfg):
        return {k: v for k, v in cfg.items() if k not in ("revision", "evaluation")}

    if (
        not config["evaluation"].get("primary_attempt_only")
        or execution(original) != execution(config)
        or original["evaluation"]["tasks"] != config["evaluation"]["tasks"]
        or original_binding["protocol"] != identity()
        or original_binding["student_execution"] != execution_identity(config)
    ):
        raise ValueError("Primary import execution conditions differ")
    expected = {m["name"]: m for m in config["evaluation"]["models"]}
    old_models = {m["name"]: m for m in original["evaluation"]["models"]}
    imports = []
    for name, model in expected.items():
        if model["adapter_sha256_after"] != old_models[name]["adapter_sha256_after"]:
            raise ValueError("Primary import model differs")
        for task in config["evaluation"]["tasks"]:
            path = origin / name / "tasks" / task / "attempt_1"
            if not (path / "intent.json").exists():
                continue
            worker = (
                read_json(path / "worker.json")
                if (path / "worker.json").exists()
                else None
            )
            if (
                worker
                and process_identity(worker["pid"]) == worker["identity"]
                and not (path / "evaluation_row.json").exists()
            ):
                raise ValueError("Drain active primary workers before importing")
            row = recover_attempt(
                original,
                old_models[name],
                task,
                path,
                "Interrupted primary attempt retained as Unknown",
            )
            target = Path(output) / name / "tasks" / task / "attempt_1"
            record = {
                **row,
                "binding": attempt_binding(config, model, task),
                "directory": str(target),
                "episode_directory": str(path / "episode"),
                "imported_primary": {
                    "row_path": str(path / "evaluation_row.json"),
                    "row_sha256": digest(row),
                    "original_binding": row["binding"],
                    "source_lock_sha256": original_binding["source_lock_sha256"],
                    "same_execution_conditions": True,
                    "retry_results_excluded": True,
                },
            }
            result_path = target / "evaluation_row.json"
            if result_path.exists():
                if read_json(result_path) != record:
                    raise ValueError("Imported primary changed")
            else:
                write_json(result_path, record)
            selected = target.parent / "selected.json"
            selection = {
                "attempt": 1,
                "row_path": str(result_path),
                "row_sha256": digest(record),
            }
            if selected.exists():
                if read_json(selected) != selection:
                    raise ValueError("Imported selection changed")
            else:
                write_json(selected, selection)
            imports.append(record["imported_primary"])
    receipt = {
        "origin": str(origin),
        "origin_binding_sha256": digest(original_binding),
        "primary_attempts": imports,
    }
    path = Path(output) / "primary_import.json"
    if path.exists():
        if read_json(path) != receipt:
            raise ValueError("Primary import source changed")
    else:
        write_json(path, receipt)


def suite(config, deployment, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    settings = config["evaluation"]
    if any(
        settings.get(key) != value
        for key, value in {
            "split": "test",
            "attempts_per_task": 1,
            "concurrency": 4,
            "fallback_concurrency": 2,
            "max_capacity_retries": 0 if settings.get("primary_attempt_only") else 1,
            "record_api_outcomes": True,
            "teacher_calls": 0,
            "parameter_updates": 0,
            "split_manifest_sha256": plan()["manifest_sha256"],
        }.items()
    ) or [r["name"] for r in settings["models"]] != ["S0", "u060", "u100"]:
        raise ValueError("Frozen comparison settings changed")
    if config["evaluation"]["revision"] != REVISION or config["evaluation"][
        "tasks"
    ] != evaluation_tasks("test", "all"):
        raise ValueError("Expected the frozen test111 order")
    if (
        len(config["evaluation"]["tasks"]) != 111
        or config["evaluation"]["rubric"] != RUBRIC
    ):
        raise ValueError("Test111/rubric identity differs")
    identities = {}
    checkpoint_files = {}
    for model in config["evaluation"]["models"]:
        receipt = verify_checkpoint(
            deployment["models"][model["name"]], config["model"]
        )
        if receipt["adapter_sha256_after"] != model["adapter_sha256_after"]:
            raise ValueError("Frozen evaluation weights differ")
        if model.get("adapter_file_sha256"):
            from recoveragent.demonstrations import sha256

            value = sha256(
                Path(deployment["models"][model["name"]]) / "adapter_model.safetensors"
            )
            if value != model["adapter_file_sha256"] or (
                model["name"] == "S0" and receipt.get("formal_s0") is not True
            ):
                raise ValueError("Adapter file SHA/formal S0 receipt differs")
            checkpoint_files[model["name"]] = value
        identities[model["name"]] = digest(receipt)
    binding = {
        "config": config,
        "checkpoints": identities,
        "checkpoint_files": checkpoint_files,
        "final_test_protocol": CODEACT_TEST,
        "appworld_revision": config["appworld_revision"],
        "protocol": identity(),
        "student_execution": execution_identity(config),
        "split_sha256": plan()["manifest_sha256"],
        "source_lock_sha256": verify_source(deployment),
    }
    with (output / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (output / "binding.json").exists():
            if read_json(output / "binding.json") != binding:
                raise ValueError("Test comparison binding changed")
        else:
            write_json(output / "binding.json", binding)
        import_primary_attempts(config, deployment, output)
        start = (
            read_json(output / "summary.json").get("started", time.time())
            if (output / "summary.json").exists()
            else time.time()
        )
        completed = {}
        for model in config["evaluation"]["models"]:
            replace_json(
                output / "summary.json",
                {
                    "status": "running",
                    "started": start,
                    "active_model": model["name"],
                    "completed_models": completed,
                },
            )
            completed[model["name"]] = evaluate_model(
                config, deployment, model, output / model["name"]
            )
            comparison(output, config)
        replace_json(
            output / "summary.json",
            {
                "status": "complete",
                "started": start,
                "finished": time.time(),
                "completed_models": completed,
            },
        )
        return completed


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config, deployment = read_json(args.config), read_json(args.deployment)
    verify_source(deployment)
    if os.environ.get("RECOVERAGENT_S2_EXECUTION"):
        raise ValueError("Old S2 execution must not enter new CodeAct evaluation")
    suite(config, deployment, args.output)


if __name__ == "__main__":
    main()
