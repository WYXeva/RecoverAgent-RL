"""Attempt only the unattempted C244 suffix; preserve the partial prior evidence.

The 46 prior tasks are represented by their sealed aggregate, never fabricated
per-task rows. New task archives are published incrementally for local return.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tarfile
import time

from . import full732_c244 as original
from . import m1_vgpu_inference as inference
from .demonstrations import sha256
from .io import read_json, write_json
from .student_evaluation import request_json

PRIOR = original.ROOT / "reports/full732_c244_final_stop_20261030.json"
START_INDEX = 46


def summary(config, rows):
    prior = read_json(PRIOR)["C244"]
    expected = config["cases"][START_INDEX : START_INDEX + len(rows)]
    if [r["task_id"] for r in rows] != [r["task_id"] for r in expected]:
        raise ValueError("Suffix tasks changed or repeated")
    valid = [
        r
        for r in rows
        if r["status"] == "valid" and type(r.get("official_success")) is bool
    ]
    successes = sum(r["official_success"] is True for r in valid)
    return {
        "status": "RUNNING",
        "runtime_variant": inference.VARIANT,
        "fixed_denominator": 244,
        "historical_aggregate": prior,
        "historical_raw_artifact_return_complete": False,
        "prior_summary_sha256": sha256(PRIOR),
        "new_rows": rows,
        "attempted": START_INDEX + len(rows),
        "new_attempted": len(rows),
        "valid": prior["valid"] + len(valid),
        "success": prior["success"] + successes,
        "failure": prior["failure"] + len(valid) - successes,
        "unknown": prior["unknown"] + len(rows) - len(valid),
        "all_tasks_attempted": START_INDEX + len(rows) == 244,
        "complete_valid_evaluation": False,
        "TGC": None,
        "unattempted_task_ids": [
            c["task_id"] for c in config["cases"][START_INDEX + len(rows) :]
        ],
        "training": False,
        "checkpoint_selection": False,
        "official_benchmark_claim": False,
    }


def stop(process):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def run(args):
    lock = inference.verify_lock()
    config = read_json(args.config)
    original.validate_config(config)
    prior = read_json(PRIOR)
    if (
        prior["C244"]["attempted"] != START_INDEX
        or config["cases"][START_INDEX - 1]["task_id"] != "321ec38_1"
        or config["m1_parameter_hash"] != prior["M1_parameter_hash"]
    ):
        raise ValueError("Historical cutoff or accepted M1 changed")
    probe = read_json(args.probe)
    inference.validate_probe(probe, config["m1_parameter_hash"])
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "tasks").mkdir()
    (output / "task_archives").mkdir()
    (output / "claims").mkdir()
    write_json(output / "config.json", config)
    write_json(output / "prior_stop.json", prior)
    started = time.time()
    write_json(
        output / "started.json",
        {
            "time": started,
            "source_lock_sha256": lock,
            "start_index": START_INDEX,
            "shutdown_after_backup": args.shutdown_after_backup,
            "probe_sha256": sha256(args.probe),
        },
    )
    env = dict(
        os.environ,
        PYTHONPATH=str(original.ROOT / "src"),
        HF_HOME=str(Path(args.model_cache).resolve()),
        HF_HUB_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        OMP_NUM_THREADS="4",
        MKL_NUM_THREADS="4",
        PYTHONUNBUFFERED="1",
    )
    backend, active = None, None
    current_case = None
    rows, archives = [], []
    fatal = None
    try:
        with (output / "backend.log").open("w") as log:
            backend = subprocess.Popen(
                [
                    args.inference_python,
                    "-m",
                    "recoveragent.m1_vgpu_inference",
                    "serve",
                    "--adapter",
                    args.adapter,
                    "--probe",
                    args.probe,
                    "--parameter-hash",
                    config["m1_parameter_hash"],
                    "--output",
                    str(output / "backend"),
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        ready_until = time.monotonic() + 120
        while True:
            if backend.poll() is not None:
                raise RuntimeError("Backend exited during startup")
            try:
                identity = request_json(config["inference_url"], timeout=2)
                if identity != config["expected_policy"]:
                    raise ValueError("Wrong backend identity")
                break
            except (OSError, TimeoutError):
                if time.monotonic() >= ready_until:
                    raise TimeoutError("Backend readiness deadline")
                time.sleep(1)
        write_json(
            output / "backend_ready.json",
            request_json(config["inference_url"] + "/runtime", timeout=5),
        )
        for index in range(START_INDEX, len(config["cases"])):
            case = config["cases"][index]
            current_case = case
            task = case["task_id"]
            target = output / "tasks" / task
            write_json(
                output / "active.json",
                {"global_index": index + 1, "task_id": task, "started": time.time()},
            )
            write_json(
                output / "claims" / (task + ".json"), read_json(output / "active.json")
            )
            with (output / (task + ".log")).open("x") as log:
                active = subprocess.Popen(
                    [
                        args.appworld_python,
                        "-m",
                        "recoveragent.m1_vgpu_inference",
                        "worker",
                        "--config",
                        str(output / "config.json"),
                        "--case-index",
                        str(index),
                        "--output",
                        str(target),
                    ],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    if active.wait(
                        timeout=config["student_limits"]["wall_seconds"] + 180
                    ):
                        raise RuntimeError("Worker returned nonzero")
                    row = read_json(target / "full732_result.json")
                except (
                    subprocess.SubprocessError,
                    OSError,
                    ValueError,
                    RuntimeError,
                ) as e:
                    stop(active)
                    row = {
                        **case,
                        "status": "infrastructure_error",
                        "official_success": None,
                        "error_type": type(e).__name__,
                        "usage_unknown": True,
                    }
                    target.mkdir(exist_ok=True)
                    write_json(target / "controller_error.json", row)
            active = None
            rows.append(row)
            archive = output / "task_archives" / (task + ".tar.gz")
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(target, arcname="tasks/" + task)
                tar.add(output / (task + ".log"), arcname=task + ".log")
            archives.append(
                {
                    "task_id": task,
                    "path": str(archive.relative_to(output)),
                    "bytes": archive.stat().st_size,
                    "sha256": sha256(archive),
                }
            )
            write_json(output / "artifact_index.json", archives)
            write_json(output / "summary.json", summary(config, rows))
            print(
                json.dumps(
                    {
                        "attempted": START_INDEX + len(rows),
                        "task": task,
                        "status": row["status"],
                        "success": row["official_success"],
                    }
                ),
                flush=True,
            )
            if row["status"] == "infrastructure_error":
                fatal = "new_infrastructure_failure"
                break
    except BaseException as e:
        fatal = type(e).__name__
        write_json(output / "controller_failure.json", {"error_type": type(e).__name__})
    finally:
        stop(active)
        stop(backend)
        if current_case is not None and (
            not rows or rows[-1]["task_id"] != current_case["task_id"]
        ):
            task = current_case["task_id"]
            target = output / "tasks" / task
            target.mkdir(exist_ok=True)
            row = {
                **current_case,
                "status": "infrastructure_error",
                "official_success": None,
                "error_type": fatal or "controller_interrupted",
                "usage_unknown": True,
            }
            rows.append(row)
            write_json(target / "controller_interrupted.json", row)
            archive = output / "task_archives" / (task + ".tar.gz")
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(target, arcname="tasks/" + task)
                if (output / (task + ".log")).exists():
                    tar.add(output / (task + ".log"), arcname=task + ".log")
            archives.append(
                {
                    "task_id": task,
                    "path": str(archive.relative_to(output)),
                    "bytes": archive.stat().st_size,
                    "sha256": sha256(archive),
                }
            )
            write_json(output / "artifact_index.json", archives)
        result = summary(config, rows)
        result.update(
            status="ATTEMPTS_COMPLETE"
            if len(rows) == 244 - START_INDEX and not fatal
            else "STOPPED",
            stop_reason=fatal,
            started_unix=started,
            finished_unix=time.time(),
            source_lock_sha256=lock,
        )
        write_json(output / "summary.json", result)
        write_json(
            output / "finished.json",
            {
                "summary_sha256": sha256(output / "summary.json"),
                "new_archives": len(archives),
                "status": result["status"],
            },
        )
    if args.shutdown_after_backup:
        # Local monitor returns archives during execution. Wait at most ten
        # minutes for the final hash-bound acknowledgement, then stop billing
        # even if the client disconnected. All remote evidence remains on disk.
        deadline = time.monotonic() + 600
        verified = False
        while time.monotonic() < deadline:
            ack = output / "local_backup_ack.json"
            if ack.exists():
                data = read_json(ack)
                if data.get("summary_sha256") == sha256(
                    output / "summary.json"
                ) and data.get("archive_count") == len(archives):
                    verified = True
                    break
            time.sleep(2)
        write_json(
            output / "shutdown_receipt.json",
            {
                "requested_unix": time.time(),
                "local_return_acknowledged": verified,
                "remote_evidence_preserved": True,
                "command": "/usr/bin/shutdown",
                "user_authorized": True,
            },
        )
        subprocess.run(["/usr/bin/shutdown"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "config",
        "output",
        "adapter",
        "probe",
        "inference-python",
        "appworld-python",
        "model-cache",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--shutdown-after-backup", action="store_true")
    args = parser.parse_args()

    def interrupted(signum, frame):
        raise InterruptedError("Controller received a termination signal")

    signal.signal(signal.SIGTERM, interrupted)
    run(args)


if __name__ == "__main__":
    main()
