"""Serial process-isolated train510 queue, immutable attempts and restartable index."""

from collections import defaultdict
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from recoveragent.io import digest, read_json, write_json
from recoveragent.v2_tasks import plan

from .protocol import TOOLS, identity, now


def interrupted_usage(directory, config):
    """Do not assign zero cost when a worker died after making real requests."""
    from recoveragent.teacher import response_usage, usage_cost

    totals = {
        "requests": 0,
        "prompt_tokens": 0,
        "generated_tokens": 0,
        "cached_prompt_tokens": 0,
        "priced_cost": 0.0,
        "unknown_requests": 0,
    }
    for request in (Path(directory) / "requests").glob("*.request.json"):
        totals["requests"] += 1
        response = request.with_name(
            request.name.replace(".request.json", ".response.json")
        )
        try:
            usage = response_usage(read_json(response))
        except (FileNotFoundError, ValueError, KeyError):
            totals["unknown_requests"] += 1
            totals["priced_cost"] += usage_cost(
                {
                    "prompt_tokens": config["teacher"]["billing_context_tokens"],
                    "generated_tokens": read_json(request)["max_tokens"],
                    "cached_prompt_tokens": 0,
                },
                config["teacher"]["pricing"],
            )
            continue
        for key in ("prompt_tokens", "generated_tokens", "cached_prompt_tokens"):
            totals[key] += usage[key]
        totals["priced_cost"] += usage_cost(usage, config["teacher"]["pricing"])
    return totals


def await_existing_worker(target, timeout):
    """A restarted controller never consumes an orphan worker's running attempt."""
    path = target / "worker.json"
    if not path.exists():
        return
    pid = read_json(path)["pid"]
    started = now()
    while (Path(f"/proc/{pid}/cmdline")).exists():
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
        except FileNotFoundError:
            return
        if (
            str(target).encode() not in command
            or b"recoveragent.codeact.cli" not in command
        ):
            return
        if (target / "result.json").exists():
            return
        if now() - started >= timeout:
            raise TimeoutError(
                "Previous attempt worker still active; no second worker started"
            )
        time.sleep(2)


def queue():
    scenes = defaultdict(list)
    for row in plan()["rows"]:
        if row["role"] == "train":
            scenes[row["scenario_id"]].append(row)
    return [rows[i] for i in range(3) for rows in scenes.values()]


def replace_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def summary(directory):
    attempts = []
    for file in sorted(Path(directory).glob("episodes/*/attempt_*/result.json")):
        attempts.append({"path": str(file.parent.resolve()), **read_json(file)})
    successes = {a["task_id"] for a in attempts if a.get("success") is True}
    finished = {
        a["task_id"] for a in attempts if a.get("success") is True or a["attempt"] == 2
    }
    skipped = {
        read_json(p)["task_id"] for p in Path(directory).glob("scoring_skips/*.json")
    }
    finished |= skipped
    totals = {
        k: sum(a.get("usage", {}).get(k, 0) for a in attempts)
        for k in (
            "requests",
            "prompt_tokens",
            "generated_tokens",
            "cached_prompt_tokens",
            "priced_cost",
            "provider_cost",
        )
    }
    return {
        "attempts": attempts,
        "successful_tasks": len(successes),
        "eligible_tasks": len({a["task_id"] for a in attempts if a.get("eligible")}),
        "finished_tasks": len(finished),
        "attempted_tasks": len({a["task_id"] for a in attempts}),
        "valid_failed_attempts": sum(a.get("success") is False for a in attempts),
        "invalid_attempts": sum(a.get("success") is None for a in attempts),
        "skipped_scoring_tasks": len(skipped),
        "usage": totals,
    }


def official_scoring_error(target, result):
    """Skip evaluator exceptions only, never credential or execution faults."""
    evaluation = result.get("evaluation", {})
    trajectory_path = target / "trajectory.json"
    if (
        result.get("valid") is not False
        or result.get("success") is not None
        or evaluation.get("status") != "evaluation_error"
        or evaluation.get("category") not in (None, "unknown")
        or not trajectory_path.exists()
    ):
        return False
    trajectory = read_json(trajectory_path)
    return (
        "fault" in trajectory
        and trajectory["fault"] is None
        and digest(trajectory) == result.get("trajectory_sha256")
    )


def skip_scoring_task(directory, target, result):
    """Separate audit receipt; original official result and trajectory stay intact."""
    receipt = {
        "task_id": result["task_id"],
        "attempt": result["attempt"],
        "reason": "official_scoring_exception_skip_task",
        "success": None,
        "eligible": False,
        "result": str(target / "result.json"),
        "result_sha256": digest(result),
        "trajectory_sha256": result["trajectory_sha256"],
        "evaluation": result["evaluation"],
    }
    path = directory / "scoring_skips" / f"{result['task_id']}.json"
    if path.exists():
        if read_json(path) != receipt:
            raise ValueError("Skipped scoring evidence changed")
    else:
        write_json(path, receipt)


def collection_controls(directory, target_successes, skip_scoring_errors):
    """Scheduling is independent of immutable model/episode configuration."""
    path = directory / "collection_controls.json"
    controls = (
        read_json(path)
        if path.exists()
        else {
            "revision": "codeact_collection_controls_v1",
            "target_successes": None,
            "skip_scoring_errors": False,
        }
    )
    requested = dict(controls)
    if target_successes is not None:
        if target_successes <= 0:
            raise ValueError("target_successes must be positive")
        requested["target_successes"] = target_successes
    if skip_scoring_errors is not None:
        requested["skip_scoring_errors"] = skip_scoring_errors
    if path.exists():
        if requested != controls:
            raise ValueError("Collection controls differ from the saved continuation")
    else:
        write_json(path, requested)
    return requested


def finish_at_target(directory, config, controls):
    target = controls["target_successes"]
    totals = summary(directory)
    if target is None or totals["eligible_tasks"] < target:
        return False
    status = totals | {"status": "target_reached", "controls": controls}
    replace_json(directory / "progress.json", status)
    from .data import export

    try:
        status["export"] = export(
            directory, directory / f"target_{target}_data", config
        )
    except Exception as exc:
        replace_json(
            directory / "progress.json",
            status | {"status": "target_reached_export_error", "error": repr(exc)},
        )
        raise
    replace_json(directory / "progress.json", status)
    return True


def audit_first_batch(directory, rows):
    """Structural health gate, not a required task success rate."""
    selected = {r["task_id"] for r in rows}
    data = summary(directory)
    attempts = [a for a in data["attempts"] if a["task_id"] in selected]
    problems = []
    for attempt in attempts:
        p = Path(attempt["path"])
        if not (p / "trajectory.json").exists():
            problems.append(f"{attempt['task_id']}: missing trajectory")
            continue
        trajectory = read_json(p / "trajectory.json")
        if trajectory["protocol"] != identity() or trajectory.get("fault"):
            problems.append(f"{attempt['task_id']}: protocol/infrastructure fault")
        skip_path = Path(directory) / "scoring_skips" / f"{attempt['task_id']}.json"
        if not attempt["valid"] and not (
            skip_path.exists() and official_scoring_error(p, attempt)
        ):
            problems.append(f"{attempt['task_id']}: invalid official evaluation")
        for request in (p / "requests").glob("*.request.json"):
            if read_json(request)["tools"] != TOOLS:
                problems.append(f"{attempt['task_id']}: native tool schema changed")
    # Validate actual token export before automatically progressing past the first20.
    from .data import export

    export_receipt = export(
        directory,
        Path(directory) / "first20_data",
        read_json(Path(directory) / "config.json"),
        tasks=selected,
    )
    return {
        "tasks": len(selected),
        "attempts": len(attempts),
        "problems": problems,
        "export": export_receipt,
        "passed": not problems,
    }


def collect(
    config,
    directory,
    root,
    credentials,
    *,
    target_successes=None,
    skip_scoring_errors=None,
):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding = {
            "protocol": identity(),
            "config_sha256": digest(config),
            "queue": queue(),
        }
        if (directory / "binding.json").exists():
            if read_json(directory / "binding.json") != binding:
                raise ValueError(
                    "Collection config/protocol/queue changed; use a new directory"
                )
        else:
            config_path = directory / "config.json"
            if config_path.exists() and read_json(config_path) != config:
                raise ValueError("Pre-staged collection configuration differs")
            write_json(directory / "binding.json", binding)
            if not config_path.exists():
                write_json(config_path, config)
        controls = collection_controls(directory, target_successes, skip_scoring_errors)
        for position, row in enumerate(
            binding["queue"][: config["collection"]["max_tasks"]]
        ):
            if finish_at_target(directory, config, controls):
                return
            for attempt in range(1, config["collection"]["max_attempts_per_task"] + 1):
                target = directory / "episodes" / row["task_id"] / f"attempt_{attempt}"
                if target.exists() and not (target / "result.json").exists():
                    await_existing_worker(
                        target, config["collection"]["worker_timeout_seconds"]
                    )
                if (target / "result.json").exists():
                    result = read_json(target / "result.json")
                    if controls["skip_scoring_errors"] and official_scoring_error(
                        target, result
                    ):
                        skip_scoring_task(directory, target, result)
                        break
                    if result.get("success") is True:
                        break
                    continue
                if target.exists():
                    # A killed process is retained as an invalid attempt, never overwritten.
                    write_json(
                        target / "result.json",
                        {
                            "task_id": row["task_id"],
                            "attempt": attempt,
                            "success": None,
                            "valid": False,
                            "eligible": False,
                            "termination": "interrupted_attempt",
                            "usage": interrupted_usage(target, config),
                        },
                    )
                    continue
                totals = summary(directory)
                if (
                    totals["usage"]["priced_cost"]
                    + config["limits"]["attempt_price_cap_usd"]
                    > config["authorization"]["max_cost"]
                ):
                    replace_json(
                        directory / "progress.json", totals | {"status": "cost_limit"}
                    )
                    return
                replace_json(
                    directory / "progress.json",
                    totals
                    | {
                        "status": "running",
                        "task_id": row["task_id"],
                        "attempt": attempt,
                        "queue_position": position,
                        "pid": os.getpid(),
                        "controls": controls,
                    },
                )
                command = [
                    sys.executable,
                    "-m",
                    "recoveragent.codeact.cli",
                    "episode",
                    "--config",
                    str(directory / "config.json"),
                    "--task",
                    row["task_id"],
                    "--attempt",
                    str(attempt),
                    "--output",
                    str(target),
                    "--root",
                    str(root),
                    "--credentials",
                    str(credentials),
                ]
                started = now()
                with (directory / f"worker_{position:03d}_{attempt}.log").open(
                    "a"
                ) as log:
                    try:
                        subprocess.run(
                            command,
                            check=True,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            timeout=config["collection"]["worker_timeout_seconds"],
                        )
                    except (
                        subprocess.CalledProcessError,
                        subprocess.TimeoutExpired,
                    ) as exc:
                        target.mkdir(parents=True, exist_ok=True)
                        if not (target / "result.json").exists():
                            write_json(
                                target / "result.json",
                                {
                                    "task_id": row["task_id"],
                                    "attempt": attempt,
                                    "success": None,
                                    "valid": False,
                                    "eligible": False,
                                    "termination": type(exc).__name__,
                                    "usage": interrupted_usage(target, config),
                                    "seconds": now() - started,
                                },
                            )
                result = read_json(target / "result.json")
                if not result["valid"]:
                    if controls["skip_scoring_errors"] and official_scoring_error(
                        target, result
                    ):
                        skip_scoring_task(directory, target, result)
                        break
                    # Do not burn the 510 queue on a common dependency/credential fault.
                    replace_json(
                        directory / "progress.json",
                        summary(directory)
                        | {"status": "blocked", "blocked_attempt": str(target)},
                    )
                    return
                if result["success"] is True:
                    break
            replace_json(
                directory / "progress.json",
                summary(directory)
                | {
                    "status": "running",
                    "completed_queue_positions": position + 1,
                    "controls": controls,
                },
            )
            if position + 1 == config["collection"]["first_batch_tasks"]:
                audit_path = directory / "first_batch_audit.json"
                if audit_path.exists():
                    audit = read_json(audit_path)
                else:
                    audit = audit_first_batch(
                        directory, binding["queue"][: position + 1]
                    )
                    write_json(audit_path, audit)
                if not audit["passed"]:
                    replace_json(
                        directory / "progress.json",
                        summary(directory) | {"status": "first_batch_blocked"},
                    )
                    return
            time.sleep(0.1)
        if finish_at_target(directory, config, controls):
            return
        replace_json(
            directory / "progress.json",
            summary(directory)
            | {
                "status": "queue_exhausted"
                if controls["target_successes"]
                else "complete",
                "controls": controls,
            },
        )
