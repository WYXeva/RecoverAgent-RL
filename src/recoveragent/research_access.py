"""Reviewed private custom research use; official benchmark restrictions stay intact.

Authority is the installed data license plus the frozen A membership, never the
old manifest's editable READY/BLOCKED field. No task contents or old demos here.
"""

from pathlib import Path

from . import APPWORLD_REVISION
from .demonstrations import sha256
from .io import digest, read_json
from .unified_split import validate_split

ROOT = Path(__file__).resolve().parents[2]
SCOPE = "custom_full732_research_a244_fresh_v1"
FORMAT = "clean-teacher-custom-full732-v1"
SPLIT = ROOT / "configs/unified_task_split_full_pool_candidate.json"
SPLIT_SHA = "0466cec29d9ad770a1cfeacb4c93945e5fcbcad68b9e980c5264d447418121a2"
LICENSE_SHA = "3786bdb398d95201c067c3b59a3943659a36783579f70ad0fab99f915b499609"


def a_tasks():
    if sha256(SPLIT) != SPLIT_SHA:
        raise ValueError("Frozen Full732 split changed")
    plan = read_json(SPLIT)
    validate_split(plan)
    rows = sorted(
        (r for r in plan["rows"] if r["role"] == "base_sft"), key=lambda r: r["task_id"]
    )
    if len(rows) != 244 or len({r["scenario_id"] for r in rows}) != 244:
        raise ValueError("A244 membership is incomplete")
    return plan, rows


def validate_identity(task, context):
    plan, rows = a_tasks()
    row = next((r for r in rows if r["task_id"] == task), None)
    expected = (
        None
        if row is None
        else {
            "scope": SCOPE,
            "task_id": task,
            "official_split": row["official_split"],
            "role": "base_sft",
            "split_manifest_sha256": plan["manifest_sha256"],
            "source_row_sha256": digest(row),
            "public_redistribution": False,
            "official_benchmark_claim": False,
        }
    )
    if expected is None or context != expected:
        raise ValueError("Unreviewed research scope, source or non-A task")
    return row


def context_for(row):
    plan, _ = a_tasks()
    context = {
        "scope": SCOPE,
        "task_id": row["task_id"],
        "official_split": row["official_split"],
        "role": "base_sft",
        "split_manifest_sha256": plan["manifest_sha256"],
        "source_row_sha256": digest(row),
        "public_redistribution": False,
        "official_benchmark_claim": False,
    }
    validate_identity(row["task_id"], context)
    return context


def authorize_task(root, task, context):
    row = validate_identity(task, context)
    root = Path(root) / "data"
    plan, _ = a_tasks()
    if sha256(root / "LICENSE") != LICENSE_SHA:
        raise ValueError("Installed data use terms differ from reviewed license")
    if (root / "version.txt").read_text().strip() != plan["source"]["data_version"]:
        raise ValueError("Data version changed")
    source = root / "datasets" / (row["official_split"] + ".txt")
    if sha256(source) != plan["source"]["split_files"][row["official_split"]]:
        raise ValueError("Original split provenance changed")
    if task not in source.read_text().splitlines():
        raise ValueError("Task not in its original split")
    if plan["source"]["appworld_revision"] != APPWORLD_REVISION:
        raise ValueError("Unreviewed AppWorld revision")
    directory = root / "tasks" / task
    for name in (
        "specs.json",
        "ground_truth/evaluation.py",
        "ground_truth/metadata.json",
    ):
        if not (directory / name).is_file():
            raise ValueError("Task initialization/evaluation artifact unavailable")
    return row["official_split"]


def validate_record(record):
    from .execution_v6 import SYSTEM_PROMPT
    from .answer_guard import PROTOCOL as ANSWER_PROTOCOL
    from .harness import PROTOCOL as HARNESS_PROTOCOL, REFACTOR_REVISION

    row = validate_identity(record["task_id"], record["custom_research"])
    if (
        record["split"] != row["official_split"]
        or record["scenario_id"] != row["scenario_id"]
    ):
        raise ValueError("Research record relabels original provenance")
    if (
        record.get("actor_prompt_variant") != "execution_v6"
        or record.get("tool_mode") != "dynamic_discovery"
        or record["messages"][0] != {"role": "system", "content": SYSTEM_PROMPT}
    ):
        raise ValueError("Research demonstration uses an unfrozen execution protocol")
    if (
        record.get("answer_guard", {}).get("protocol") != ANSWER_PROTOCOL
        or record.get("harness", {}).get("protocol") != HARNESS_PROTOCOL
        or record.get("harness", {}).get("runtime_revision") != REFACTOR_REVISION
    ):
        raise ValueError("Research demonstration lacks actual declared controls")
