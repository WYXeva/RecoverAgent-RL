"""One clean-initial-state evaluation protocol for three separately trained stages.

Reuse the existing frozen Student worker; no recovery checkpoint is accepted as
an evaluation start. Preparation/aggregation do not load weights or call models.
"""

import copy
from collections import Counter
from pathlib import Path
import subprocess
import sys

from .io import digest, read_json, write_json
from .unified_split import (
    sha,
    stage_tasks,
    validate_training_sources,
    verify_runtime_data,
)

STAGES = ("M1", "M2", "M3")
TRAINING_STAGE = dict(zip(STAGES, ("base_sft", "recovery_sft", "grpo"), strict=True))


def verify_lock(path):
    lock = read_json(path)
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha(name) != expected
        ):
            raise ValueError("Shared-stage source/config lock mismatch: " + name)
    return sha(path)


def verify_release(path, expected_sha, plan, stage):
    """Verify lineage and dataset membership, in addition to future training gates.

    Training receipts remain required: this structural gate alone does not prove
    gradients, rollout logprobs, recovery correctness or successful model reload.
    """
    if sha(path) != expected_sha:
        raise ValueError("Checkpoint release checksum mismatch")
    release = read_json(path)
    if (
        not {
            "stage",
            "split_manifest_sha256",
            "policy",
            "training_sources",
            "training_evidence",
            "initialization",
        }
        <= release.keys()
    ):
        raise ValueError(
            "New-stage checkpoint release required; historical release is ineligible"
        )
    if (
        release["stage"] != stage
        or release["split_manifest_sha256"] != plan["manifest_sha256"]
    ):
        raise ValueError("Wrong stage/split checkpoint")
    if not release.get("parameter_update_verified") or not release.get(
        "save_reload_verified"
    ):
        raise ValueError("Unaccepted training checkpoint")
    if not release["policy"]["checkpoint_id"].startswith("adapter:"):
        raise ValueError("Trained LoRA identity required")
    sources = release["training_sources"]
    if sha(sources["path"]) != sources["sha256"]:
        raise ValueError("Training source receipt changed")
    validate_training_sources(plan, TRAINING_STAGE[stage], read_json(sources["path"]))
    evidence = release["training_evidence"]
    if not evidence or any(sha(p) != h for p, h in evidence.items()):
        raise ValueError("Training acceptance evidence missing/changed")
    if stage == "M1":
        if release["initialization"] != "raw_Qwen3-4B" or release.get("parent_release"):
            raise ValueError(
                "M1 must start from raw weights, not contaminated historical LoRA"
            )
    else:
        previous = STAGES[STAGES.index(stage) - 1]
        parent = release["parent_release"]
        parent_release = verify_release(
            parent["path"], parent["sha256"], plan, previous
        )
        if release["initialization"] != parent_release["policy"]["checkpoint_id"]:
            raise ValueError("Training lineage mismatch")
        if (
            release["policy"]["checkpoint_id"]
            == parent_release["policy"]["checkpoint_id"]
        ):
            raise ValueError("Stage weights have not changed from parent")
    return release


def prepare(protocol, plan, stage, release_path, release_sha, inference_url):
    if (
        protocol["split_manifest_sha256"] != plan["manifest_sha256"]
        or stage not in STAGES
    ):
        raise ValueError("Protocol/split/stage mismatch")
    preflight = protocol.get("scoring_preflight")
    if preflight:
        if sha(preflight["path"]) != preflight["sha256"]:
            raise ValueError("Scoring preflight evidence changed")
        if read_json(preflight["path"])["scoring_status"] != "PASS":
            raise ValueError(
                "Phase 0 scoring boundary remains unresolved; do not start later stages"
            )
    verify_runtime_data(plan, protocol["execution"]["appworld_root"])
    release = verify_release(release_path, release_sha, plan, stage)
    identity = release["policy"]
    if {k: identity[k] for k in protocol["model_identity"]} != protocol[
        "model_identity"
    ]:
        raise ValueError("Model/revision/template/thinking mismatch")
    config = copy.deepcopy(protocol["execution"])
    config.update(
        expected_policy=identity,
        inference_url=inference_url,
        cases=[
            {
                "task_id": r["task_id"],
                "scenario_id": r["scenario_id"],
                "split": r["official_split"],
                "partition": "shared_validation",
            }
            for r in stage_tasks(plan, "validation")
        ],
    )
    return {
        "format": "shared-stage-run-v1",
        "stage": stage,
        "protocol_sha256": digest(protocol),
        "split_manifest_sha256": plan["manifest_sha256"],
        "release_path": str(release_path),
        "release_sha256": release_sha,
        "worker_config": config,
        "start_kind": "clean_original_task",
        "resume_checkpoint": None,
    }


def collect(run, output):
    """Explicit future execution entry. All tasks run unless service/worker fails.

    Per-task model failure never changes the frozen validation membership.
    Each worker starts a fresh process and original official task state.
    """
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    write_json(target / "run.json", run)
    config = run["worker_config"]
    write_json(target / "config.json", config)
    rows = []
    for i, case in enumerate(config["cases"]):
        folder = target / ("base_sft_shared_validation_" + case["task_id"])
        try:
            with (target / (case["task_id"] + ".log")).open("x") as log:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "recoveragent.student_evaluation",
                        "worker",
                        "--config",
                        str((target / "config.json").resolve()),
                        "--case-index",
                        str(i),
                        "--arm",
                        "base_sft",
                        "--output",
                        str(folder.resolve()),
                    ],
                    check=True,
                    timeout=config["student_limits"]["wall_seconds"] + 180,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            row = read_json(folder / "result.json")
        except (OSError, subprocess.SubprocessError) as exc:
            row = {
                **case,
                "status": "infrastructure_error",
                "official_success": None,
                "error_type": type(exc).__name__,
                "usage_unknown": True,
            }
            write_json(target / (case["task_id"] + ".worker_error.json"), row)
        rows.append(row)
        print(case["task_id"], row["status"], flush=True)
        if row["status"] == "infrastructure_error":
            break  # Preserve unknown usage; never retry possibly billed requests.
    write_json(
        target / "summary.json",
        {"rows": rows, "unattempted": config["cases"][len(rows) :]},
    )


def compare(protocol, plan, stage_rows, coverage_rows=None):
    """Paired rows from verified raw receipts, including a cell for missing runs."""
    if protocol["split_manifest_sha256"] != plan["manifest_sha256"]:
        raise ValueError("Comparison protocol/split mismatch")
    expected = stage_tasks(plan, "validation")
    ids = [r["task_id"] for r in expected]
    maps = {}
    for stage, rows in stage_rows.items():
        if (
            stage not in STAGES
            or len({r["task_id"] for r in rows}) != len(rows)
            or not {r["task_id"] for r in rows} <= set(ids)
        ):
            raise ValueError("Unknown stage, repeated or foreign validation task")
        for row in rows:
            valid = (
                row.get("status") == "valid"
                and type(row.get("official_success")) is bool
            )
            outcome = (
                "infrastructure_error"
                if row.get("status") == "infrastructure_error"
                else "evaluation_unknown"
                if not valid
                else "success"
                if row["official_success"]
                else "task_failure"
            )
            if row["outcome"] != outcome:
                raise ValueError("Outcome contradicts official result availability")
        maps[stage] = {r["task_id"]: r for r in rows}
    matrix, mismatch = [], []
    for case in expected:
        cells = {stage: maps.get(stage, {}).get(case["task_id"]) for stage in STAGES}
        available = [v for v in cells.values() if v is not None]
        paired = len(available) == 3
        for field in (
            "initial_state_hash",
            "first_input_hash",
            "first_messages_hash",
            "first_tools_hash",
        ):
            values = [v.get(field) for v in available]
            if available and (any(v is None for v in values) or len(set(values)) != 1):
                mismatch.append({"task_id": case["task_id"], "field": field})
                paired = False
        matrix.append(
            {
                "task_id": case["task_id"],
                "scenario_id": case["scenario_id"],
                "paired_initial_state_and_input": paired,
                "stages": {s: v or {"outcome": "not_run"} for s, v in cells.items()},
            }
        )
    totals = {}
    for stage in STAGES:
        rows = list(maps.get(stage, {}).values())
        counts = Counter(r["outcome"] for r in rows)
        complete = len(rows) == len(ids) and counts["success"] + counts[
            "task_failure"
        ] == len(ids)
        totals[stage] = {
            "planned": len(ids),
            "attempted": len(rows),
            "successes": counts["success"],
            "failures": counts["task_failure"],
            "evaluation_unknown": counts["evaluation_unknown"],
            "infrastructure_errors": counts["infrastructure_error"],
            "not_run": len(ids) - len(rows),
            "complete": complete,
            "TGC": counts["success"] / len(ids) if complete else None,
            "success_fraction_lower_bound": counts["success"] / len(ids),
            "unknown_usage_tasks": sum(
                r.get("unknown_generation_reservation") is None
                or r.get("unknown_generation_reservation", 0) > 0
                or r["outcome"] == "infrastructure_error"
                for r in rows
            ),
            "unknown_generation_reservation": sum(
                r.get("unknown_generation_reservation", 0) or 0 for r in rows
            ),
            "cost_note": "Receipt-based counts; incomplete/unknown execution is not zero consumption. GPU billing not available here.",
            "costs": {
                key: sum(r.get(key, 0) or 0 for r in rows)
                for key in (
                    "model_requests",
                    "tool_call_intents",
                    "input_tokens",
                    "generated_tokens",
                    "wall_seconds",
                    "business_errors",
                    "schema_errors",
                    "context_exhausted",
                    "budget_exhausted",
                )
            },
        }
        totals[stage]["schema_error_rate_per_model_request"] = (
            totals[stage]["costs"]["schema_errors"]
            / totals[stage]["costs"]["model_requests"]
            if totals[stage]["costs"]["model_requests"]
            else None
        )
    complete = all(t["complete"] for t in totals.values()) and not mismatch
    differences = {}
    for previous, current in (("M1", "M2"), ("M2", "M3"), ("M1", "M3")):
        differences[current + "_minus_" + previous] = (
            totals[current]["TGC"] - totals[previous]["TGC"] if complete else None
        )
    coverage_by_task = {r["task_id"]: r for r in (coverage_rows or [])}
    by_type = {}
    labels = sorted(
        {label for r in coverage_by_task.values() for label in r["text_labels"]}
    )
    for label in labels:
        members = [
            t
            for t in ids
            if label in coverage_by_task.get(t, {}).get("text_labels", [])
        ]
        if not members:
            continue
        by_type[label] = {}
        for stage in STAGES:
            selected = [
                maps.get(stage, {}).get(t, {"outcome": "not_run"}) for t in members
            ]
            counts = Counter(r["outcome"] for r in selected)
            valid = counts["success"] + counts["task_failure"] == len(members)
            by_type[label][stage] = {
                "planned": len(members),
                "outcomes": dict(counts),
                "TGC": counts["success"] / len(members) if valid else None,
            }
    return {
        "format": "shared-stage-comparison-v1",
        "protocol_sha256": digest(protocol),
        "split_manifest_sha256": plan["manifest_sha256"],
        "status": "COMPLETE" if complete else "INCOMPLETE",
        "totals": totals,
        "by_public_text_type": by_type,
        "coverage_annotation_available": coverage_rows is not None,
        "deltas": differences,
        "matrix": matrix,
        "pairing_errors": mismatch,
        "interpretation": "Development validation of known scenarios, not official SGC or unseen-scenario generalization. No CI estimated.",
    }
