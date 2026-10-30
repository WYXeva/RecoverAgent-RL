"""Run fixed C244 only after final M1 training/reload gates; no automatic retries."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error

from recoveragent.full732_c244 import ROOT, verified_checkpoint, verify_lock
from recoveragent.io import read_json, write_json
from recoveragent.student_evaluation import request_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in (
        "training-run",
        "training-python",
        "appworld-python",
        "appworld-root",
        "model-cache",
        "output",
    ):
        p.add_argument("--" + name, required=True)
    a = p.parse_args()
    training = Path(a.training_run).resolve()
    summary = read_json(training / "summary.json")
    if summary["status"] != "PASS" or not summary["M1_trained_and_reloaded"]:
        raise ValueError("M1 not accepted; C244 must not start")
    checkpoint = training / "formal"
    report = verified_checkpoint(checkpoint)
    lock = verify_lock()
    output = Path(a.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        HF_HOME=str(Path(a.model_cache).resolve()),
        HF_HUB_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        OMP_NUM_THREADS="4",
        MKL_NUM_THREADS="4",
        PYTHONUNBUFFERED="1",
    )
    receipt = {
        "status": "RUNNING",
        "started_unix": time.time(),
        "runtime": lock,
        "m1_parameter_hash": report["trainable_after_hash"],
        "training": False,
    }
    write_json(output / "started.json", receipt)
    backend = None
    try:
        with (output / "prepare.log").open("x") as log:
            subprocess.run(
                [
                    a.appworld_python,
                    "-m",
                    "recoveragent.full732_c244",
                    "prepare",
                    "--checkpoint",
                    str(checkpoint),
                    "--root",
                    a.appworld_root,
                    "--output",
                    str(output / "config.json"),
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=300,
            )
        command = [
            a.training_python,
            "-m",
            "recoveragent.full732_m1_backend",
            "--adapter",
            str(checkpoint),
            "--port",
            "8768",
        ]
        with (output / "backend.log").open("x") as log:
            backend = subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
            )
        write_json(
            output / "backend_launch.json", {"command": command, "pid": backend.pid}
        )
        deadline = time.monotonic() + 180
        identity = None
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                raise RuntimeError("M1 inference backend exited before readiness")
            try:
                identity = request_json("http://127.0.0.1:8768", timeout=2)
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(1)
        if identity != read_json(output / "config.json")["expected_policy"]:
            raise RuntimeError("M1 inference identity/readiness failed")
        write_json(output / "backend_identity.json", identity)
        with (output / "collect.log").open("x") as log:
            subprocess.run(
                [
                    a.appworld_python,
                    "-m",
                    "recoveragent.full732_c244",
                    "collect",
                    "--config",
                    str(output / "config.json"),
                    "--output",
                    str(output / "tasks"),
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=244 * 780 + 300,
            )
        result = read_json(output / "tasks/summary.json")
        receipt.update(
            status="COMPLETE" if result["attempted"] == 244 else "STOPPED",
            attempted=result["attempted"],
            valid=result["valid"],
            success=result["success"],
            unknown=result["unknown"],
            TGC=result["TGC"],
        )
    except BaseException as exc:
        receipt.update(status="STOPPED", error_type=type(exc).__name__)
        raise
    finally:
        if backend is not None and backend.poll() is None:
            backend.terminate()
            try:
                backend.wait(timeout=30)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait()
        receipt["finished_unix"] = time.time()
        write_json(output / "summary.json", receipt)
        print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
