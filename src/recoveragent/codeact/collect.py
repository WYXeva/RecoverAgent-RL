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
        "usage": totals,
    }


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
        if not attempt["valid"]:
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


def collect(config, directory, root, credentials):
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
        for position, row in enumerate(
            binding["queue"][: config["collection"]["max_tasks"]]
        ):
            for attempt in range(1, config["collection"]["max_attempts_per_task"] + 1):
                target = directory / "episodes" / row["task_id"] / f"attempt_{attempt}"
                if target.exists() and not (target / "result.json").exists():
                    await_existing_worker(
                        target, config["collection"]["worker_timeout_seconds"]
                    )
                if (target / "result.json").exists():
                    if read_json(target / "result.json").get("success") is True:
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
                | {"status": "running", "completed_queue_positions": position + 1},
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
        replace_json(
            directory / "progress.json", summary(directory) | {"status": "complete"}
        )
