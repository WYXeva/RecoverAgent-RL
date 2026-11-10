"""Scenario-isolated v2 identities, derived only from frozen public metadata."""

import hashlib
from collections import Counter
from pathlib import Path

from .demonstrations import sha256
from .io import digest, read_json
from .research_access import SPLIT, SPLIT_SHA, authorize_artifacts

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "configs/v2_scenario_split_20261109.json"
SCOPE = "full732_scenario_isolated_v2_20261109"
RUNTIME = "v2_learning_round1_coevolve_goals_20261110"
SEED = 20261109


def rank(value):
    return hashlib.sha256(f"{SEED}:{value}".encode()).hexdigest()


def build_plan():
    if sha256(SPLIT) != SPLIT_SHA:
        raise ValueError("Original Full732 metadata changed")
    original = read_json(SPLIT)
    scenes = sorted({r["scenario_id"] for r in original["rows"]}, key=rank)
    if len(scenes) != 244:
        raise ValueError("Expected 244 scenarios")
    roles = {
        scene: "train" if i < 170 else "dev" if i < 207 else "test"
        for i, scene in enumerate(scenes)
    }
    rows = sorted(
        [
            {k: r[k] for k in ("task_id", "scenario_id", "official_split")}
            | {"role": roles[r["scenario_id"]]}
            for r in original["rows"]
        ],
        key=lambda r: (rank(r["scenario_id"]), rank(r["task_id"])),
    )
    if Counter(r["scenario_id"] for r in rows) != dict.fromkeys(scenes, 3):
        raise ValueError("Expected three variants per scenario")
    plan = {
        "format": SCOPE,
        "seed": SEED,
        "source_manifest_sha256": SPLIT_SHA,
        "source": original["source"],
        "rows": rows,
        "feedback20": [
            next(r["task_id"] for r in rows if r["scenario_id"] == s)
            for s in scenes[:20]
        ],
        "dev24": [r["task_id"] for r in rows if r["scenario_id"] in scenes[170:178]],
        "official_benchmark_claim": False,
        "assignment": "sha256(seed:scenario); 170/37/37; no outcomes or task content",
    }
    return {**plan, "manifest_sha256": digest(plan)}


def plan():
    frozen = read_json(PLAN)
    if frozen != build_plan():
        raise ValueError("v2 scenario assignment or fixed task lists changed")
    return frozen


def context_for(task_id, *, training=False):
    frozen = plan()
    row = next((r for r in frozen["rows"] if r["task_id"] == task_id), None)
    if row is None or row["role"] == "test" or (training and row["role"] != "train"):
        raise ValueError("Task is outside this v2 execution/training scope")
    return {
        "scope": SCOPE,
        **row,
        "split_manifest_sha256": frozen["manifest_sha256"],
        "source_row_sha256": digest(row),
        "public_redistribution": False,
        "official_benchmark_claim": False,
    }


def validate_context(task_id, context, *, training=False):
    expected = context_for(task_id, training=training)
    if context != expected:
        raise ValueError("v2 task identity/source/role mismatch")
    return expected


def authorize_task(root, task_id, context):
    expected = validate_context(task_id, context)
    return authorize_artifacts(root, task_id, expected, plan())


def development_tasks(count):
    """Freeze reduced dev by original order, one task per scenario, no outcomes."""
    frozen = plan()
    if count == 24:
        return frozen["dev24"]
    if count != 3:
        raise ValueError("Only the approved dev24 or three-scenario dev3 is supported")
    rows = {r["task_id"]: r for r in frozen["rows"]}
    chosen, seen = [], set()
    for task in frozen["dev24"]:
        scenario = rows[task]["scenario_id"]
        if scenario not in seen:
            chosen.append(task)
            seen.add(scenario)
        if len(chosen) == count:
            return chosen
    raise ValueError("Insufficient frozen dev scenarios")


def grpo_schedule(cfg, generated):
    originals = plan()["feedback20"][: cfg["feedback_tasks"]]
    schedule = []
    for i in range(cfg["grpo"]["groups"]):
        if i % 4 == 3:
            if not generated:
                continue  # Missing generated slot is reported, never replaced to fill a quota.
            task = generated[(i // 4) % len(generated)]
            schedule.append(
                {
                    "source": "generated",
                    "task_id": task["source_task_id"],
                    "generated_task": task,
                }
            )
        else:
            schedule.append(
                {"source": "original", "task_id": originals[i % len(originals)]}
            )
    return schedule
