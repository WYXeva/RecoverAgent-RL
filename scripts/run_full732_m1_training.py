"""Remote-only sequential M1 driver. No retries, downloads, or task evaluation."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from recoveragent.demonstrations import sha256
from recoveragent.full732_sft_data import MANIFEST, ROOT, prepare
from recoveragent.full732_sft_runtime import verify_lock
from recoveragent.io import read_json, write_json


def run_stage(command, source, env, log_path, gpu_path, timeout):
    started = time.monotonic()
    with log_path.open("x") as log, gpu_path.open("x") as gpu:
        gpu.write("unix_time,memory_used_MiB,utilization_percent,power_W\n")
        proc = subprocess.Popen(
            command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            while proc.poll() is None:
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Training stage limit reached")
                sample = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used,utilization.gpu,power.draw",
                        "--format=csv,noheader,nounits",
                    ],
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
                gpu.write(str(time.time()) + "," + sample.stdout.strip() + "\n")
                gpu.flush()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            if proc.returncode:
                raise RuntimeError("Stage failed; inspect retained training log")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    return time.monotonic() - started


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True)
    p.add_argument("--archive", required=True)
    p.add_argument("--model-cache", required=True)
    p.add_argument("--appworld-python", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    output, dataset = Path(a.output).resolve(), Path(a.dataset).resolve()
    if output == dataset or dataset in output.parents:
        raise ValueError("Output must not modify the source data package")
    output.mkdir(parents=True, exist_ok=False)
    os.environ.update(
        PYTHONPATH=str(ROOT / "src"),
        HF_HUB_OFFLINE="1",
        HF_HOME=str(Path(a.model_cache).resolve()),
        TOKENIZERS_PARALLELISM="false",
        OMP_NUM_THREADS="4",
        MKL_NUM_THREADS="4",
    )
    env = dict(os.environ)
    summary = {
        "status": "RUNNING",
        "source_manifest_sha256": MANIFEST,
        "stages": [],
        "started_unix": time.time(),
        "training_started": False,
    }
    write_json(output / "started.json", summary)
    stage = "environment"
    try:
        summary["runtime_lock_sha256"] = verify_lock()
        probe = (
            "import importlib.metadata as m,json; "
            "d=m.distribution('appworld'); "
            "direct=d.read_text('direct_url.json'); "
            "assert '42b5bcf3cd334fee33f0c37c02070a9f5807add5' in direct; "
            "assert m.version('SQLAlchemy') == '2.0.52'; "
            "print(json.dumps({'appworld':d.version,'SQLAlchemy':m.version('SQLAlchemy')}))"
        )
        for label, python in [
            ("training", sys.executable),
            ("appworld", a.appworld_python),
        ]:
            result = subprocess.run(
                [python, "-m", "pip", "freeze"],
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            (output / f"{label}_environment.txt").write_text(result.stdout)
        result = subprocess.run(
            [a.appworld_python, "-c", probe],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        write_json(output / "appworld_environment.json", json.loads(result.stdout))
        stage = "data_import_and_retokenization"
        prepared = output / "dataset"
        prepare(dataset, Path(a.archive).resolve(), prepared)
        prepared_sha = sha256(prepared / "manifest.json")
        summary["prepared_manifest_sha256"] = prepared_sha
        prior = []
        for stage, seconds in [
            ("short_smoke", 1800),
            ("long_smoke", 1800),
            ("formal", 21600),
        ]:
            command = [
                sys.executable,
                "-m",
                "recoveragent.full732_sft_runtime",
                "--config",
                str(ROOT / "configs" / f"full732_m1_{stage}.json"),
                "--dataset",
                str(prepared),
                "--prepared-manifest-sha256",
                prepared_sha,
                "--output",
                str(output / stage),
            ]
            for path in prior:
                command.extend(["--prerequisite", str(path)])
            write_json(
                output / f"{stage}_command.json",
                {"command": command, "timeout": seconds},
            )
            elapsed = run_stage(
                command,
                ROOT,
                env,
                output / f"{stage}.log",
                output / f"{stage}_gpu.csv",
                seconds,
            )
            report = output / stage / "recoveragent_checkpoint.json"
            receipt = read_json(report)
            if (
                receipt["status"] != "PASS"
                or not receipt["parameter_update_and_reload_pass"]
            ):
                raise ValueError("Training health/reload gate failed")
            entry = {
                "stage": stage,
                "status": "PASS",
                "wall_seconds": elapsed,
                "optimizer_steps": receipt["optimizer_steps"],
                "receipt_sha256": sha256(report),
            }
            write_json(output / f"{stage}_completed.json", entry)
            summary["stages"].append(entry)
            summary["training_started"] = True
            prior.append(report)
        summary.update(
            status="PASS", M1_trained_and_reloaded=True, C244_evaluated=False
        )
    except BaseException as exc:
        # Includes interruption; do not infer completion or launch another stage.
        summary.update(
            status="STOPPED", unfinished_stage=stage, error_type=type(exc).__name__
        )
        raise
    finally:
        summary["training_started"] = any(
            (output / name / "training_started.json").exists()
            for name in ("short_smoke", "long_smoke", "formal")
        )
        summary["finished_unix"] = time.time()
        write_json(output / "summary.json", summary)


if __name__ == "__main__":
    main()
