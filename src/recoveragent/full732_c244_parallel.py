"""Two isolated M1 inference processes; inherit C244 receipts without retries.

Only transport and scheduling differ from the sealed serial C244 implementation.
Each AppWorld task and each model RNG remain in their own process. The original
model, generation code, prompt, scoring, task list, and budgets are unchanged.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.error

from . import full732_c244 as serial
from . import full732_m1_backend as original_backend
from .demonstrations import sha256
from .io import files_manifest, read_json, write_json
from .student_evaluation import request_json

ROOT = serial.ROOT
LOCK = ROOT / "configs/full732_c244_parallel_20261030_lock.json"
PORTS = (8768, 8769)


def verify_lock():
    serial.verify_lock()
    lock = read_json(LOCK)
    if lock["serial_lock_sha256"] != sha256(serial.LOCK):
        raise ValueError("Serial C244 source lock changed")
    for name, expected in lock["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Invalid parallel source path")
        if sha256(ROOT / name) != expected:
            raise ValueError("Parallel source changed: " + name)
    return sha256(LOCK)


def validate_lane(config):
    url = config["inference_url"]
    if url not in [f"http://127.0.0.1:{p}" for p in PORTS]:
        raise ValueError("Only the two private local endpoints are allowed")
    canonical = dict(config, inference_url="http://127.0.0.1:8768")
    serial.validate_config(canonical)
    return PORTS.index(int(url.rsplit(":", 1)[1]))


def summarize(config, rows):
    expected = {c["task_id"]: c for c in config["cases"]}
    by_id = {}
    for row in rows:
        task = row["task_id"]
        if task not in expected or task in by_id:
            raise ValueError("Duplicate or foreign C244 result")
        if row["split"] != expected[task]["split"]:
            raise ValueError("C244 provenance changed")
        by_id[task] = row
    ordered = [by_id[c["task_id"]] for c in config["cases"] if c["task_id"] in by_id]
    result = serial.summarize(config, ordered)
    result["unattempted"] = [c for c in config["cases"] if c["task_id"] not in by_id]
    result["max_concurrent_tasks"] = 2
    return result


def backend(args):
    if args.port not in PORTS:
        raise ValueError("Invalid parallel lane")
    module = serial.isolated_module(original_backend, "_parallel_m1_backend")
    server = module.HTTPServer
    errors = Path(args.error_log)

    def bind_lane(address, handler):
        if address != ("127.0.0.1", 8768):
            raise ValueError("Unexpected original backend binding")
        original_respond = handler.respond

        def respond(instance, value, status=200):
            if status >= 400:
                with errors.open("a") as stream:
                    stream.write(
                        json.dumps({"time": time.time(), "status": status, **value})
                        + "\n"
                    )
                    stream.flush()
            return original_respond(instance, value, status)

        handler.respond = respond
        return server(("127.0.0.1", args.port), handler)

    module.HTTPServer = bind_lane
    # Reuse the exact sealed model loading, validation and generation function.
    # Its canonical argument remains 8768; only the socket binding is adapted.
    sys.argv = [sys.argv[0], "--adapter", args.adapter, "--port", "8768"]
    module.main()


def worker(args, lock):
    config = read_json(args.config)
    lane = validate_lane(config)
    result = serial.engine().run_episode(
        config, config["cases"][args.case_index], "base_sft", args.output
    )
    result["engine_scope"] = result.pop("scope")
    result.update(
        scope=serial.SCOPE,
        stage="M1",
        official_benchmark_claim=False,
        parallel_scheduler={
            "lane": lane,
            "max_concurrent_tasks": 2,
            "source_lock_sha256": lock,
        },
    )
    write_json(Path(args.output) / "full732_result.json", result)


def inherited_rows(previous, config, handoff):
    """Require an ordered, sealed prefix; never adopt an incomplete task."""
    if handoff["previous"] != str(previous) or not handoff["serial_processes_exited"]:
        raise ValueError("Missing completed serial handoff")
    if sha256(previous / "config.json") != handoff["config_sha256"]:
        raise ValueError("Serial config changed after handoff")
    if not handoff["user_requested_parallel"]:
        raise ValueError("Parallel scheduling was not requested")
    rows = []
    for i, case in enumerate(config["cases"]):
        task = case["task_id"]
        directory = previous / "tasks" / task
        if i >= handoff["completed_count"]:
            if directory.exists():
                raise ValueError("Unresolved serial attempt; no automatic retry")
            continue
        row = read_json(directory / "full732_result.json")
        if row["task_id"] != task or row["status"] not in {"valid", "evaluation_error"}:
            raise ValueError("Unaccepted serial handoff row")
        if row["policy"] != config["expected_policy"]:
            raise ValueError("Inherited task used a different M1")
        if files_manifest(directory) != handoff["task_files"][task]:
            raise ValueError("Inherited C244 evidence changed")
        rows.append(row)
    if len(rows) != handoff["completed_count"]:
        raise ValueError("Incomplete inherited prefix")
    return rows


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if process.poll() is None:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


def run(args, lock):
    previous = Path(args.previous).resolve()
    config = read_json(previous / "config.json")
    serial.validate_config(config)
    checkpoint = Path(args.training_run).resolve() / "formal"
    accepted = serial.verified_checkpoint(checkpoint)
    if config["m1_parameter_hash"] != accepted["trainable_after_hash"]:
        raise ValueError("Parallel policy differs from accepted M1")
    handoff = read_json(args.handoff)
    rows = inherited_rows(previous, config, handoff)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    tasks = output / "tasks"
    tasks.mkdir()
    for row in rows:
        source = previous / "tasks" / row["task_id"]
        target = tasks / row["task_id"]
        shutil.copytree(source, target)
        if files_manifest(target) != handoff["task_files"][row["task_id"]]:
            raise ValueError("Inherited C244 copy differs")
    write_json(output / "handoff.json", handoff)
    write_json(output / "config.json", config)
    write_json(tasks / "config.json", config)
    receipt = {
        "status": "RUNNING",
        "started_unix": time.time(),
        "inherited_tasks": len(rows),
        "source_lock_sha256": lock,
        "max_concurrent_tasks": 2,
        "checkpoint_selection": False,
        "training": False,
        "m1_parameter_hash": accepted["trainable_after_hash"],
    }
    write_json(output / "started.json", receipt)
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        HF_HOME=str(Path(args.model_cache).resolve()),
        HF_HUB_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        OMP_NUM_THREADS="4",
        MKL_NUM_THREADS="4",
        PYTHONUNBUFFERED="1",
    )
    servers, active, logs = {}, {}, []
    next_index = len(rows)
    fatal = None

    def save_progress():
        value = summarize(config, rows)
        value["active_task_ids"] = [
            config["cases"][v["index"]]["task_id"] for v in active.values()
        ]
        write_json(tasks / "progress" / f"{len(rows):03d}.json", value)

    def unknown(index, reason):
        return dict(
            config["cases"][index],
            status="infrastructure_error",
            official_success=None,
            error_type=reason,
            usage_unknown=True,
        )

    try:
        gpu = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        fields = [s.strip() for s in gpu.stdout.strip().split(",")]
        if (
            len(fields) != 3
            or "A100" not in fields[0]
            or int(fields[1]) < 78000
            or int(fields[2]) < 70000
        ):
            raise ValueError("Two-lane A100 memory preflight failed")
        write_json(
            output / "gpu_preflight.json",
            {
                "name": fields[0],
                "total_MiB": int(fields[1]),
                "free_MiB": int(fields[2]),
            },
        )
        for lane, port in enumerate(PORTS):
            lane_config = dict(config, inference_url=f"http://127.0.0.1:{port}")
            validate_lane(lane_config)
            write_json(output / f"lane_{lane}.json", lane_config)
            log = (output / f"backend_{lane}.log").open("x")
            logs.append(log)
            command = [
                args.training_python,
                "-m",
                "recoveragent.full732_c244_parallel",
                "backend",
                "--adapter",
                str(checkpoint),
                "--port",
                str(port),
                "--error-log",
                str(output / f"backend_errors_{lane}.jsonl"),
            ]
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            servers[lane] = process
            write_json(
                output / f"backend_launch_{lane}.json",
                {"pid": process.pid, "command": command},
            )
            deadline = time.monotonic() + 180
            while True:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Parallel backend failed readiness")
                try:
                    identity = request_json(lane_config["inference_url"], timeout=2)
                    break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(1)
            if identity != config["expected_policy"]:
                raise ValueError("Parallel backend policy identity differs")
            write_json(output / f"backend_identity_{lane}.json", identity)
        write_json(output / "backend_identity.json", config["expected_policy"])
        save_progress()
        while next_index < 244 or active:
            for lane, process in servers.items():
                errors = output / f"backend_errors_{lane}.jsonl"
                if process.poll() is not None or (
                    errors.exists() and errors.stat().st_size
                ):
                    fatal = "inference_backend_failure"
            if fatal:
                break
            for lane in range(len(PORTS)):
                if lane in active or next_index >= 244:
                    continue
                case = config["cases"][next_index]
                target = tasks / case["task_id"]
                write_json(
                    output / "claims" / (case["task_id"] + ".json"),
                    {
                        "case_index": next_index,
                        "lane": lane,
                        "started_unix": time.time(),
                    },
                )
                log = (tasks / (case["task_id"] + ".log")).open("x")
                logs.append(log)
                command = [
                    args.appworld_python,
                    "-m",
                    "recoveragent.full732_c244_parallel",
                    "worker",
                    "--config",
                    str(output / f"lane_{lane}.json"),
                    "--case-index",
                    str(next_index),
                    "--output",
                    str(target),
                ]
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                active[lane] = {
                    "process": process,
                    "index": next_index,
                    "started": time.monotonic(),
                }
                next_index += 1
            for lane, item in list(active.items()):
                process, index = item["process"], item["index"]
                timeout = (
                    time.monotonic() - item["started"]
                    > config["student_limits"]["wall_seconds"] + 180
                )
                if process.poll() is None and not timeout:
                    continue
                if timeout or process.returncode != 0:
                    stop_group(process)
                path = tasks / config["cases"][index]["task_id"] / "full732_result.json"
                if process.returncode == 0 and path.exists():
                    row = read_json(path)
                else:
                    row = unknown(
                        index, "worker_timeout" if timeout else "worker_process_error"
                    )
                rows.append(row)
                del active[lane]
                save_progress()
                print(
                    json.dumps(
                        {
                            "completed": len(rows),
                            "task": row["task_id"],
                            "status": row["status"],
                        }
                    ),
                    flush=True,
                )
                if row["status"] == "infrastructure_error":
                    fatal = "worker_infrastructure_failure"
            if fatal:
                break
            time.sleep(1)
        if fatal:
            receipt["stop_reason"] = fatal
        receipt["status"] = "STOPPED" if fatal else "COMPLETE"
    except BaseException as exc:
        receipt.update(status="STOPPED", error_type=type(exc).__name__)
        raise
    finally:
        for process in servers.values():
            stop_group(process)
        for item in active.values():
            stop_group(item["process"])
            path = (
                tasks
                / config["cases"][item["index"]]["task_id"]
                / "full732_result.json"
            )
            rows.append(
                read_json(path)
                if path.exists()
                else unknown(item["index"], "stopped_after_peer_failure")
            )
        for log in logs:
            log.close()
        result = summarize(config, rows)
        write_json(tasks / "summary.json", result)
        receipt.update(
            {k: result[k] for k in ("attempted", "valid", "success", "unknown", "TGC")}
        )
        receipt["finished_unix"] = time.time()
        write_json(output / "summary.json", receipt)
        print(json.dumps(receipt), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["run", "backend", "worker"])
    for name in (
        "previous",
        "handoff",
        "training-run",
        "training-python",
        "appworld-python",
        "model-cache",
        "output",
        "config",
        "adapter",
        "error-log",
    ):
        p.add_argument("--" + name)
    p.add_argument("--port", type=int)
    p.add_argument("--case-index", type=int)
    args = p.parse_args()
    lock = verify_lock()
    if args.command == "backend":
        backend(args)
    elif args.command == "worker":
        worker(args, lock)
    else:
        run(args, lock)


if __name__ == "__main__":
    main()
