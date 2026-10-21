"""Bounded serial coverage campaign using the frozen v5 Teacher runner. No training."""

import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

from recoveragent.coverage_sampling import coverage_plan
from recoveragent.dataset_design import design, family_split
from recoveragent.dataset_sources import discover, scan
from recoveragent.demonstrations import sha256
from recoveragent.io import digest, read_json, write_json
from recoveragent.protocol_lock import verify_lock
from recoveragent.sampling import official_pool


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--credentials-file", required=True)
    p.add_argument("--campaign", required=True)
    args = p.parse_args()
    campaign = read_json(args.campaign)
    template = read_json(args.config)
    verify_lock(template)
    pool = official_pool(template["appworld_root"])
    split = family_split(pool, campaign["split_seed"], campaign["validation_families"])
    split_path = Path("configs") / (campaign["name"] + "_split.json")
    if split_path.exists():
        assert read_json(split_path) == split
    else:
        write_json(split_path, split)
    initial = Path(campaign["existing_inventory"])
    quality = list(read_json(campaign["existing_quality"])["rows"])
    current = initial
    completed = []
    for batch in range(1, campaign["max_batches"] + 1):
        prefix = f"{campaign['name']}_b{batch:02d}"
        run = Path("runs") / prefix
        samples = [
            json.loads(line)
            for line in (current / "samples.jsonl").read_text().splitlines()
        ]
        selected = design(samples, quality, max_per_task=campaign["max_per_task"])
        ids = set(selected["selected_ids"])
        chosen = [s for s in samples if s["trajectory_id"] in ids]
        coverage = {
            "trajectories": len(chosen),
            "tasks": len({s["task_id"] for s in chosen}),
            "families": len({s["scenario_id"] for s in chosen}),
        }
        if (
            coverage["trajectories"] >= campaign["target_trajectories"]
            and coverage["tasks"] >= campaign["target_tasks"]
            and coverage["families"] >= campaign["target_families"]
        ):
            break
        attempts = [
            a for r in completed for a in read_json(r / "clean_dataset/attempts.json")
        ]
        ledger = {
            "pool_hash": digest(sorted(pool.items())),
            "selected_success_counts": dict(Counter(s["task_id"] for s in chosen)),
            "new_attempt_counts": dict(Counter(a["task_id"] for a in attempts)),
            "source_inventory_sha256": sha256(current / "inventory.json"),
            "source_quality_hash": digest(quality),
            "history": [
                {
                    "trajectory_id": a["trajectory_id"],
                    "task_id": a["task_id"],
                    "official_success": a["official_success"],
                    "accepted": a["accepted"],
                }
                for a in attempts
            ],
        }
        eligible = [
            t
            for t in pool
            if ledger["selected_success_counts"].get(t, 0) < campaign["max_per_task"]
            and ledger["new_attempt_counts"].get(t, 0)
            < campaign["max_new_attempts_per_task"]
        ]
        if not eligible:
            break
        n = min(campaign["batch_size"], len(eligible))
        ledger_path = f"reports/{prefix}_coverage.json"
        settings = {
            "strategy": "coverage_balanced",
            "seed": campaign["seed"] + batch,
            "num_tasks": n,
            "coverage_ledger": ledger_path,
            "coverage_ledger_hash": digest(ledger),
            "max_per_task": campaign["max_per_task"],
            "max_new_attempts_per_task": campaign["max_new_attempts_per_task"],
        }
        plan = coverage_plan(pool, settings, ledger)
        config = copy.deepcopy(template)
        config.update(
            compare_tool_modes=False,
            tool_mode="dynamic_discovery",
            sampling=settings,
            task_manifest=f"configs/{prefix}_tasks.json",
            task_manifest_hash=digest(plan),
        )
        config["limits"].update(
            max_tasks=n,
            max_requests=40 * n,
            total_generated_tokens=16384 * n,
            total_wall_seconds=600 * n,
        )
        config["authorization"]["max_cost"] = n
        config["authorization"]["reference"] = (
            "User authorized formal Teacher dataset campaign; existing DeepSeek 费用不限 authorization, bounded batches."
        )
        actual = Path(".cache") / (prefix + ".json")
        public = copy.deepcopy(config)
        public["authorization"] = {
            "approved": False,
            "reference": None,
            "currency": "USD",
            "max_cost": None,
        }
        # No secrets in this public configuration. Pricing is an estimate, not a bill.
        if not run.exists():
            write_json(ledger_path, ledger)
            write_json(config["task_manifest"], plan)
            write_json(f"configs/{prefix}.json", public)
            write_json(actual, config)
            print(
                json.dumps(
                    {
                        "batch": batch,
                        "pre_run_coverage": coverage,
                        "tasks": plan["tasks"],
                        "plan_hash": digest(plan),
                    }
                ),
                flush=True,
            )
            with Path(f".cache/{prefix}_collect.log").open("x") as log:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "recoveragent.p3a",
                        "collect",
                        "--config",
                        str(actual),
                        "--output",
                        str(run),
                        "--credentials-file",
                        args.credentials_file,
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
        elif not (run / "summary.json").exists():
            raise RuntimeError(
                "Interrupted run must be audited, never overwritten: " + str(run)
            )
        else:
            if read_json(run / "config.json") != config:
                raise ValueError("Resume config/history changed")
        summary = read_json(run / "summary.json")
        if not (run / "clean_dataset.verified.json").exists():
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "recoveragent.demonstrations",
                    "verify",
                    str(run / "clean_dataset"),
                    "--write-receipt",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        report = Path("reports") / (prefix + "_quality.json")
        if not report.exists():
            env = dict(
                os.environ,
                HF_HOME=str(Path(".cache/huggingface").resolve()),
                HF_HUB_OFFLINE="1",
            )
            with Path(f".cache/{prefix}_quality.log").open("x") as log:
                subprocess.run(
                    [
                        campaign["tokenizer_python"],
                        "-m",
                        "recoveragent.sft_quality",
                        str(run / "clean_dataset"),
                        "--expected-manifest-sha256",
                        sha256(run / "clean_dataset/manifest.json"),
                        "--output",
                        f".cache/{prefix}_tokens",
                        "--report",
                        str(report),
                    ],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
        quality.extend(read_json(report)["rows"])
        completed.append(run)
        current = Path(".cache") / (prefix + "_inventory")
        if not current.exists():
            scan(discover("."), pool, current)
        summary.update(batch=batch, run_id=run.name, quality_report=str(report))
        write_json(f"reports/{prefix}_summary.json", summary) if not Path(
            f"reports/{prefix}_summary.json"
        ).exists() else None
        print(json.dumps(summary), flush=True)
    samples = [
        json.loads(line)
        for line in (current / "samples.jsonl").read_text().splitlines()
    ]
    final_design = design(samples, quality, max_per_task=campaign["max_per_task"])
    result = {
        "campaign": campaign,
        "inventory": str(current),
        "quality_rows": quality,
        "design": final_design,
        "completed_runs": [str(r) for r in completed],
        "training_started": False,
    }
    write_json(".cache/" + campaign["name"] + "_result.json", result)
    print(
        json.dumps(
            {
                "campaign_complete": True,
                "selected_trajectories": len(final_design["selected_ids"]),
                "new_attempts": sum(
                    read_json(r / "summary.json")["attempts"] for r in completed
                ),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
