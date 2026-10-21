"""Seal an externally stopped collection from actual completed workers, offline."""

import argparse
from pathlib import Path
import subprocess
import os

from recoveragent.dataset_design import design
from recoveragent.dataset_sources import discover, scan
from recoveragent.demonstrations import export_dataset, verify_dataset, sha256
from recoveragent.formal_dataset import json_lines
from recoveragent.io import read_json, write_json, digest
from recoveragent.sampling import official_pool, load_plan


def stopped_prefix(plan, trajectories):
    actual = [t["task_id"] for t in trajectories]
    if len(set(actual)) != len(actual) or set(actual) != set(
        plan["tasks"][: len(actual)]
    ):
        raise ValueError("Stopped workers must be exactly the planned task prefix")
    return plan["tasks"][: len(actual)], plan["tasks"][len(actual) :]


def seal(campaign_path, run_path):
    campaign = read_json(campaign_path)
    run = Path(run_path)
    config = read_json(run / "config.json")
    plan = load_plan(config)
    diagnostic = read_json("reports/base_sft_provider_diagnostic.json")
    if diagnostic["http_status"] != 402:
        raise ValueError("Confirmed provider balance failure required")
    stopped = read_json(".cache/base_sft_stopped_controllers.json")
    read_json(".cache/base_sft_terminated_controllers.json")
    for worker in run.glob("teacher_*.config.json"):
        if not (
            run / worker.name.removesuffix(".config.json") / "trajectory.json"
        ).exists():
            raise ValueError("A started worker has not preserved its trajectory")
    trajectories = [read_json(p) for p in sorted(run.glob("teacher_*/trajectory.json"))]
    actual, unattempted = stopped_prefix(plan, trajectories)
    stop = {
        "reason": "provider_http_402_insufficient_balance",
        "run_id": run.name,
        "task_manifest_hash": digest(plan),
        "attempted_tasks": actual,
        "unattempted_tasks": unattempted,
        "provider_diagnostic_sha256": sha256(
            "reports/base_sft_provider_diagnostic.json"
        ),
        "controller_stopped_at_epoch": stopped["time"],
        "active_worker_interrupted": False,
        "actor_protocol_changed": False,
        "prior_transport_errors_have_no_recorded_http_status": True,
        "original_trajectories_modified": False,
        "training_started": False,
    }
    write_json(run / "collection_stop.json", stop)
    write_json("reports/base_sft_collection_interruption.json", stop)
    pool = official_pool(config["appworld_root"])
    manifest = export_dataset(run, run / "clean_dataset", set(pool), config["limits"])
    verify_dataset(run / "clean_dataset", set(pool))
    subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "recoveragent.demonstrations",
            "verify",
            str(run / "clean_dataset"),
            "--write-receipt",
        ],
        check=True,
    )
    summary = {
        "status": "PARTIAL",
        "attempts": len(trajectories),
        "dataset_id": manifest["dataset_id"],
        "counts": manifest["counts"],
        "costs": manifest["costs"],
        "wall_seconds": stopped["time"] - (run / "config.json").stat().st_mtime,
        "wall_seconds_basis": "config filesystem mtime to external stop; interrupted controller monotonic duration unavailable",
        "external_stop": stop,
        "training_started": False,
    }
    write_json(run / "summary.json", summary)
    write_json(f"reports/{run.name}_summary.json", summary)
    env = dict(
        os.environ,
        HF_HOME=str(Path(".cache/huggingface").resolve()),
        HF_HUB_OFFLINE="1",
    )
    with Path(f".cache/{run.name}_quality.log").open("x") as log:
        subprocess.run(
            [
                campaign["tokenizer_python"],
                "-m",
                "recoveragent.sft_quality",
                str(run / "clean_dataset"),
                "--expected-manifest-sha256",
                sha256(run / "clean_dataset/manifest.json"),
                "--output",
                f".cache/{run.name}_tokens",
                "--report",
                f"reports/{run.name}_quality.json",
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    completed = sorted(Path("runs").glob(campaign["name"] + "_b*"))
    if any(not (r / "summary.json").exists() for r in completed):
        raise ValueError("Another batch is still incomplete")
    quality = read_json(campaign["existing_quality"])["rows"]
    for r in completed:
        quality.extend(read_json(f"reports/{r.name}_quality.json")["rows"])
    inventory = Path(".cache") / (run.name + "_inventory")
    scan(discover("."), pool, inventory)
    samples = list(json_lines(inventory / "samples.jsonl"))
    result = {
        "campaign": campaign,
        "inventory": str(inventory),
        "quality_rows": quality,
        "design": design(samples, quality, max_per_task=campaign["max_per_task"]),
        "completed_runs": [str(r) for r in completed],
        "interruption": stop,
        "training_started": False,
    }
    write_json(".cache/" + campaign["name"] + "_result.json", result)
    return {
        "status": "SEALED_PARTIAL",
        "actual_attempts": len(trajectories),
        "unattempted": len(unattempted),
        "selected": len(result["design"]["selected_ids"]),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--campaign", required=True)
    p.add_argument("--run", required=True)
    a = p.parse_args()
    print(seal(a.campaign, a.run))
