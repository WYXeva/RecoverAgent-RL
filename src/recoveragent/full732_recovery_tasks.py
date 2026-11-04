"""Frozen B244 identities for the next collection; no task content is read here."""

from .demonstrations import sha256
from .io import digest, read_json
from .research_access import SPLIT, SPLIT_SHA
from .unified_split import validate_split

SCOPE = "custom_full732_b244_nullable_v2_20261104"


def b_tasks():
    if sha256(SPLIT) != SPLIT_SHA:
        raise ValueError("Frozen Full732 split changed")
    plan = read_json(SPLIT)
    validate_split(plan)
    rows = sorted(
        (r for r in plan["rows"] if r["role"] == "recovery_grpo"),
        key=lambda r: r["task_id"],
    )
    if len(rows) != 244 or len({r["scenario_id"] for r in rows}) != 244:
        raise ValueError("B244 membership is incomplete")
    return plan, rows


def context_for(task_id):
    plan, rows = b_tasks()
    row = next((r for r in rows if r["task_id"] == task_id), None)
    if row is None:
        raise ValueError("Recovery collection accepts frozen B244 tasks only")
    return {
        "scope": SCOPE,
        "task_id": task_id,
        "official_split": row["official_split"],
        "role": row["role"],
        "split_manifest_sha256": plan["manifest_sha256"],
        "source_row_sha256": digest(row),
        "public_redistribution": False,
        "official_benchmark_claim": False,
    }


def validate_context(task_id, context):
    expected = context_for(task_id)
    if context != expected:
        raise ValueError("B244 research identity/source mismatch")
    return expected


def authorize_task(root, task_id, context):
    from .research_access import authorize_artifacts

    expected = validate_context(task_id, context)
    plan, _ = b_tasks()
    return authorize_artifacts(root, task_id, expected, plan)
