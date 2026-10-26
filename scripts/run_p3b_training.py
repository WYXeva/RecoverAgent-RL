"""Run the authorized short/long/formal sequence; fail closed at every gate.

Use a sealed source checkout and an already verified local model cache. This
script neither downloads models nor edits any data/config/source lock.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True)
    p.add_argument("--python", required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--model-cache", required=True)
    p.add_argument("--retokenization-report", required=True)
    p.add_argument("--expected-manifest-sha256", required=True)
    p.add_argument("--output", required=True)
    p.add_argument(
        "--runtime-module",
        choices=[
            "recoveragent.sft_recipe",
            "recoveragent.sft_bf16_runtime",
            "recoveragent.sft_a100_runtime",
        ],
        default="recoveragent.sft_recipe",
    )
    a = p.parse_args()
    source, output = Path(a.source).resolve(), Path(a.output).resolve()
    dataset = Path(a.dataset).resolve()
    if (
        hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest()
        != a.expected_manifest_sha256
    ):
        raise ValueError("Unexpected data manifest")
    tokens = read(a.retokenization_report)
    if (
        tokens["status"] != "PASS"
        or not tokens["byte_identical"]
        or tokens["source_manifest_sha256"] != a.expected_manifest_sha256
    ):
        raise ValueError("Remote retokenization gate failed")
    output.mkdir(parents=True, exist_ok=False)
    env = dict(
        os.environ,
        PYTHONPATH=str(source / "src"),
        HF_HOME=str(Path(a.model_cache).resolve()),
        HF_HUB_OFFLINE="1",
        OMP_NUM_THREADS="4",
        MKL_NUM_THREADS="4",
        TOKENIZERS_PARALLELISM="false",
    )
    stages = [
        ("short_smoke", "base_sft_qwen3_4b_smoke.json", 1800),
        ("length_smoke", "base_sft_qwen3_4b_length_smoke.json", 1800),
        ("formal", "base_sft_qwen3_4b_lora.json", 21600),
    ]
    summary = {
        "status": "RUNNING",
        "manifest_sha256": a.expected_manifest_sha256,
        "stages": [],
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    write(output / "summary.json", summary)
    try:
        for stage, config, timeout in stages:
            report = output / (stage + ".json")
            command = [
                # Resolving a venv's python symlink selects the system environment.
                str(Path(a.python).absolute()),
                "-m",
                a.runtime_module,
                "execute",
                "--config",
                str(source / "configs" / config),
                "--dataset",
                str(dataset),
                "--expected-manifest-sha256",
                a.expected_manifest_sha256,
                "--output",
                str(output / stage),
                "--report",
                str(report),
            ]
            entry = {"stage": stage, "command": command, "started_unix": time.time()}
            summary["stages"].append(entry)
            write(output / "summary.json", summary)
            started = time.monotonic()
            with (
                (output / (stage + ".log")).open("x") as log,
                (output / (stage + "_gpu.csv")).open("x") as gpu,
            ):
                gpu.write("unix_time,memory_used_MiB,utilization_percent,power_W\n")
                proc = subprocess.Popen(
                    command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT
                )
                try:
                    while proc.poll() is None:
                        if time.monotonic() - started > timeout:
                            raise TimeoutError("Training stage time limit")
                        sample = subprocess.run(
                            [
                                "nvidia-smi",
                                "--query-gpu=memory.used,utilization.gpu,power.draw",
                                "--format=csv,noheader,nounits",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            check=True,
                        )
                        gpu.write(str(time.time()) + "," + sample.stdout.strip() + "\n")
                        gpu.flush()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            pass
                    entry["exit_code"] = proc.returncode
                    if proc.returncode:
                        raise RuntimeError(
                            "Training failed; inspect retained stage log"
                        )
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
            result = read(report)
            if (
                result["status"] != "PASS"
                or not result["parameter_update_and_reload_pass"]
                or not result["checkpoint_reloaded"]
                or result["dataset_manifest_sha256"] != a.expected_manifest_sha256
            ):
                raise ValueError("Training update/reload gate failed")
            entry.update(
                status="PASS",
                wall_seconds=time.monotonic() - started,
                optimizer_steps=len(result["gradient_checks"]),
            )
            write(output / "summary.json", summary)
        summary["status"] = "PASS"
    except Exception as exc:
        summary.update(status="FAILED", error_type=type(exc).__name__)
        raise
    finally:
        summary["finished_unix"] = time.time()
        write(output / "summary.json", summary)


if __name__ == "__main__":
    main()
