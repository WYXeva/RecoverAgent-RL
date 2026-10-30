"""Continue only unattempted C244 tasks after the recorded parallel timeout.

The user requested optional parallelism. Restore the original single backend,
preserve all completed/unknown attempts, and retain the original time limits.
This is not a retry path for OOM, corrupt data, or failed M1 acceptance.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error

from . import full732_c244 as serial
from . import full732_c244_parallel as parallel
from .io import files_manifest, read_json, write_json
from .demonstrations import sha256
from .student_evaluation import request_json

ROOT = serial.ROOT
PLAN = ROOT / "configs/full732_c244_serial_fallback_20261030.json"
LOCK = ROOT / "configs/full732_c244_serial_fallback_20261030_lock.json"


def verify_lock():
    parallel.verify_lock()
    lock = read_json(LOCK)
    if lock["parallel_lock_sha256"] != sha256(parallel.LOCK):
        raise ValueError("Parallel provenance changed")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("Serial fallback source changed: " + name)
    return sha256(LOCK)


def validate_prior(config, summary, plan):
    rows = summary["rows"]
    expected = config["cases"][: plan["inherited_count"]]
    if [r["task_id"] for r in rows] != [c["task_id"] for c in expected]:
        raise ValueError("Cannot skip or retry a prior C244 attempt")
    infra = {
        r["task_id"]: r.get("error_type")
        for r in rows
        if r["status"] == "infrastructure_error"
    }
    if infra != plan["allowed_prior_transport_errors"]:
        raise ValueError("Only the recorded transport failure permits this fallback")
    for row, case in zip(rows, expected, strict=True):
        if (
            row["split"] != case["split"]
            or row.get("policy", config["expected_policy"]) != config["expected_policy"]
        ):
            raise ValueError("Prior task provenance/policy changed")
        if row["status"] not in {"valid", "evaluation_error", "infrastructure_error"}:
            raise ValueError("Unclassified prior task")
    return rows


def summary(config, rows):
    result = parallel.summarize(config, rows)
    result.update(
        max_concurrent_tasks=1,
        historical_peak_concurrent_tasks=2,
        continuation="serial_after_recorded_parallel_timeout",
    )
    return result


def run(args):
    lock = verify_lock()
    plan = read_json(PLAN)
    previous = Path(args.previous).resolve()
    prior_path = previous / "tasks/summary.json"
    if sha256(prior_path) != plan["prior_summary_sha256"]:
        raise ValueError("Prior evaluation summary differs from frozen fallback plan")
    if any(p.stat().st_size for p in previous.glob("backend_errors_*.jsonl")):
        raise ValueError("No fallback from model/backend failures, including OOM")
    config = read_json(previous / "config.json")
    serial.validate_config(config)
    rows = validate_prior(config, read_json(prior_path), plan)
    checkpoint = Path(args.training_run).resolve() / "formal"
    accepted = serial.verified_checkpoint(checkpoint)
    if accepted["trainable_after_hash"] != config["m1_parameter_hash"]:
        raise ValueError("M1 identity changed")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    tasks = output / "tasks"
    tasks.mkdir()
    inherited = {}
    for row in rows:
        source = previous / "tasks" / row["task_id"]
        if row["status"] in {"valid", "evaluation_error"}:
            terminal = source / "terminal"
            for name, expected in read_json(terminal / "manifest.json")[
                "files"
            ].items():
                if sha256(terminal / name) != expected:
                    raise ValueError("Prior terminal evidence changed")
        inherited[row["task_id"]] = files_manifest(source)
        shutil.copytree(source, tasks / row["task_id"])
        if files_manifest(tasks / row["task_id"]) != inherited[row["task_id"]]:
            raise ValueError("Prior attempt copy changed")
    write_json(
        output / "inherited_sources.json",
        {
            "previous": str(previous),
            "prior_summary_sha256": sha256(prior_path),
            "task_files": inherited,
        },
    )
    write_json(output / "config.json", config)
    write_json(tasks / "config.json", config)
    receipt = {
        "status": "RUNNING",
        "started_unix": time.time(),
        "source_lock_sha256": lock,
        "inherited_tasks": len(rows),
        "max_concurrent_tasks": 1,
        "training": False,
        "checkpoint_selection": False,
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
    backend = active = None
    try:
        with (output / "backend.log").open("x") as log:
            command = [
                args.training_python,
                "-m",
                "recoveragent.full732_m1_backend",
                "--adapter",
                str(checkpoint),
                "--port",
                "8768",
            ]
            backend = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        write_json(
            output / "backend_launch.json", {"pid": backend.pid, "command": command}
        )
        deadline = time.monotonic() + 180
        while True:
            if backend.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Original single backend failed readiness")
            try:
                identity = request_json(config["inference_url"], timeout=2)
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(1)
        if identity != config["expected_policy"]:
            raise ValueError("Fallback backend identity differs")
        write_json(output / "backend_identity.json", identity)
        write_json(tasks / "progress" / f"{len(rows):03d}.json", summary(config, rows))
        for index in range(len(rows), len(config["cases"])):
            case = config["cases"][index]
            target = tasks / case["task_id"]
            write_json(
                output / "claims" / (case["task_id"] + ".json"),
                {"case_index": index, "started_unix": time.time()},
            )
            try:
                with (tasks / (case["task_id"] + ".log")).open("x") as log:
                    command = [
                        args.appworld_python,
                        "-m",
                        "recoveragent.full732_c244",
                        "worker",
                        "--config",
                        str(output / "config.json"),
                        "--case-index",
                        str(index),
                        "--output",
                        str(target),
                    ]
                    active = subprocess.Popen(
                        command,
                        cwd=ROOT,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    code = active.wait(
                        timeout=config["student_limits"]["wall_seconds"] + 180
                    )
                    if code:
                        raise subprocess.CalledProcessError(code, command)
                row = read_json(target / "full732_result.json")
            except (subprocess.SubprocessError, OSError, ValueError) as exc:
                if active is not None:
                    parallel.stop_group(active)
                row = dict(
                    case,
                    status="infrastructure_error",
                    official_success=None,
                    error_type=type(exc).__name__,
                    usage_unknown=True,
                )
                write_json(tasks / (case["task_id"] + ".worker_error.json"), row)
            active = None
            row = dict(
                row,
                serial_continuation={
                    "source_lock_sha256": lock,
                    "max_concurrent_tasks": 1,
                },
            )
            rows.append(row)
            write_json(
                tasks / "progress" / f"{len(rows):03d}.json", summary(config, rows)
            )
            print(
                {
                    "completed": len(rows),
                    "task": row["task_id"],
                    "status": row["status"],
                },
                flush=True,
            )
            if row["status"] == "infrastructure_error":
                receipt["stop_reason"] = "new_serial_infrastructure_failure"
                break
        receipt["status"] = "COMPLETE" if len(rows) == 244 else "STOPPED"
    except BaseException as exc:
        receipt.update(status="STOPPED", error_type=type(exc).__name__)
        raise
    finally:
        if active is not None:
            parallel.stop_group(active)
        if backend is not None:
            parallel.stop_group(backend)
        result = summary(config, rows)
        write_json(tasks / "summary.json", result)
        receipt.update(
            {k: result[k] for k in ("attempted", "valid", "success", "unknown", "TGC")}
        )
        receipt["finished_unix"] = time.time()
        write_json(output / "summary.json", receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "previous",
        "training-run",
        "training-python",
        "appworld-python",
        "model-cache",
        "output",
    ):
        parser.add_argument("--" + name, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
