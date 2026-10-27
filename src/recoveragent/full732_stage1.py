"""Frozen Full732 metadata/admission entry; only one scripted train probe runs.

No training/model dispatch exists here. Reported booleans are outputs, never
credentials. A future permission exception requires reviewed evidence and a new
protocol implementation, not a user-edited READY field or a self-signed manifest.
"""

import argparse
import copy
from pathlib import Path
import subprocess
import sys

from .execution_v6 import SYSTEM_PROMPT, SYSTEM_PROMPT_SHA256, actor_system
from .io import digest, read_json, write_json
from .shared_stage import verify_lock
from .unified_split import POLICY_URL, ROLES, sha, validate_split

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOL = ROOT / "configs/full732_stage1_protocol.json"
# This reviewed protocol is the trust anchor; a replacement JSON cannot grant use.
PROTOCOL_SHA256 = "a1e4491783386ede1b9d0f0d0524de9dca0d49854c60f5e67ca2ba45c1eb3730"
PURPOSE_ROLE = {
    "base_sft": "base_sft",
    "recovery_sft": "recovery_grpo",
    "grpo": "recovery_grpo",
    "validation": "shared_validation",
}


def load_contract(path=DEFAULT_PROTOCOL):
    if sha(path) != PROTOCOL_SHA256:
        raise ValueError("Unreviewed or changed Stage 1 protocol")
    protocol = read_json(path)

    def bound(reference):
        source = ROOT / reference["path"]
        if sha(source) != reference["sha256"]:
            raise ValueError("Frozen source changed: " + reference["path"])
        return read_json(source)

    plan, audit = bound(protocol["split"]), bound(protocol["audit"])
    validate_split(plan)  # Verify the existing assignments; never generate a split.
    if (
        plan["scope"] != "full_pool_candidate"
        or plan["manifest_sha256"] != protocol["split"]["manifest_sha256"]
        or plan["audit_sha256"] != digest(audit)
        or plan["source"] != audit["source"]
        or plan["counts"] != dict.fromkeys(ROLES, 244)
        or len(plan["rows"]) != 732
        or plan["grpo_reuses"] != "recovery_grpo"
        or plan["excluded"]
        or audit["anomalies"]
    ):
        raise ValueError("Full732 metadata/source identity mismatch")
    sources = {r["task_id"]: r for r in audit["rows"]}
    for row in plan["rows"]:
        original = sources[row["task_id"]]
        if (
            any(
                row[key] != original[key]
                for key in ("task_id", "scenario_id", "official_split")
            )
            or original["structural_status"] != "PASS"
        ):
            raise ValueError("Task metadata differs from original audit")
    if protocol["upstream_policy"]["source_url"] != POLICY_URL:
        raise ValueError("Missing reviewed upstream usage evidence")
    execution = bound(protocol["execution_reference"])
    bound(protocol["runtime_lock"])
    clean = bound(protocol["clean_state_evidence"])
    if (
        actor_system(execution["execution"], harness=True) != SYSTEM_PROMPT
        or execution["execution"]["system_prompt_sha256"] != SYSTEM_PROMPT_SHA256
    ):
        raise ValueError("Actor prompt identity changed")
    return protocol, plan, execution, clean


def select_tasks(purpose, path=DEFAULT_PROTOCOL):
    """Metadata only: B is shared by Recovery-SFT and GRPO; C stays unique."""
    if purpose not in PURPOSE_ROLE:
        raise ValueError("Unknown data purpose")
    _, plan, _, _ = load_contract(path)
    return [r for r in plan["rows"] if r["role"] == PURPOSE_ROLE[purpose]]


def _decision(row, purpose, policy, initial=None):
    # Re-derive from audited membership, never permission_status/runtime_verified.
    usage = (
        "taskwise_development_validation"
        if purpose == "validation"
        else "parameter_training"
    )
    allowed = row["official_split"] in policy["grants"][usage]
    return {
        "metadata_eligible": True,
        "usage_authorized": allowed,
        "usage": usage,
        "usage_evidence": policy["source_url"],
        "usage_blocker": None
        if allowed
        else "upstream_split_usage_restricted_no_reviewed_exception",
        "execution_ready": False,
        "technical_blockers": [
            "new_stage_checkpoint_not_accepted",
            "terminal_scoring_not_verified",
            "student_session_requires_legacy_prompt_declarations",
        ],
        "clean_initial_evaluation": initial.get("evaluation_status")
        if initial
        else "NOT_PROBED",
        "successful_terminal_scoring": "NOT_VERIFIED",
        "stage_execution_authorized": False,
        "dispatch_allowed": False,
    }


def inventory(path=DEFAULT_PROTOCOL):
    protocol, plan, _, clean = load_contract(path)
    initial = {r["task_id"]: r for r in clean["rows"]}
    rows = []
    for row in plan["rows"]:
        purposes = [p for p, role in PURPOSE_ROLE.items() if role == row["role"]]
        rows.append(
            {
                "task_id": row["task_id"],
                "scenario_id": row["scenario_id"],
                "official_split": row["official_split"],
                "role": row["role"],
                "purposes": purposes,
                **_decision(
                    row,
                    purposes[0],
                    protocol["upstream_policy"],
                    initial.get(row["task_id"]),
                ),
            }
        )
    return {
        "format": "full732-stage1-admission-v1",
        "scope": plan["scope"],
        "protocol_sha256": PROTOCOL_SHA256,
        "split_manifest_sha256": plan["manifest_sha256"],
        "source": plan["source"],
        "seed": plan["seed"],
        "tasks": len(rows),
        "scenarios": len({r["scenario_id"] for r in rows}),
        "counts": plan["counts"],
        "grpo_reuses": "recovery_grpo",
        "permission_counts": {
            role: {
                "allowed": sum(
                    r["usage_authorized"] for r in rows if r["role"] == role
                ),
                "blocked": sum(
                    not r["usage_authorized"] for r in rows if r["role"] == role
                ),
            }
            for role in ROLES
        },
        "rows": rows,
        "dispatch_allowed": False,
        "note": "Metadata and usage checks are not technical readiness or permission to execute later stages. Initial-state unknown does not prove terminal scoring impossible.",
    }


def probe_contract(task_id, purpose, path=DEFAULT_PROTOCOL):
    """Admission is recalculated at dispatch, never taken from an edited report."""
    protocol, plan, execution, _ = load_contract(path)
    if purpose not in PURPOSE_ROLE:
        raise ValueError("Unknown data purpose")
    row = next((r for r in plan["rows"] if r["task_id"] == task_id), None)
    if row is None or row["role"] != PURPOSE_ROLE[purpose]:
        raise ValueError("Task is absent or belongs to a different A/B/C purpose")
    decision = _decision(row, purpose, protocol["upstream_policy"])
    if not decision["usage_authorized"]:
        raise PermissionError(decision["usage_blocker"])
    if (
        task_id != protocol["probe"]["task_id"]
        or purpose != protocol["probe"]["purpose"]
        or row["official_split"] != "train"
    ):
        raise PermissionError(
            "Stage 1 permits only the fixed single train script probe"
        )
    return {
        "format": "full732-stage1-probe-contract-v1",
        "protocol_sha256": PROTOCOL_SHA256,
        "split_manifest_sha256": plan["manifest_sha256"],
        "task": copy.deepcopy(row),
        "purpose": purpose,
        "start_kind": "clean_original_task",
        "resume_checkpoint": None,
        "model_identity": execution["model_identity"],
        "execution": execution["execution"],
        "system_message": actor_system(execution["execution"], harness=True),
        "actor_request_sent": False,
        "model_performance_claim": False,
        "probe": protocol["probe"],
        "admission": decision,
    }


def _verify_probe_inputs(contract):
    """Inspect one already-admitted train task and shared public prerequisites."""
    _, plan, _, _ = load_contract()
    data = ROOT / contract["execution"]["appworld_root"] / "data"
    source = plan["source"]
    if (data / "version.txt").read_text().strip() != source["data_version"] or sha(
        data / "datasets/train.txt"
    ) != source["split_files"]["train"]:
        raise ValueError("Initial data version/train membership changed")
    for folder, field in (
        ("base_dbs", "base_db_files"),
        ("api_docs/function_calling", "tool_schema_files"),
    ):
        if {p.name: sha(p) for p in (data / folder).iterdir() if p.is_file()} != source[
            field
        ]:
            raise ValueError("Base data/public schemas changed")
    task = data / "tasks" / contract["task"]["task_id"]
    actual = digest(
        {
            str(p.relative_to(task)): sha(p)
            for p in [task / "specs.json", *sorted((task / "dbs").glob("*.jsonl"))]
        }
    )
    if actual != contract["task"]["initial_input_sha256"]:
        raise ValueError("Original train task input changed")


def run_probe(task_id, purpose, output, path=DEFAULT_PROTOCOL):
    contract = probe_contract(task_id, purpose, path)  # Before file/worker access.
    protocol, _, _, _ = load_contract(path)
    verify_lock(ROOT / protocol["runtime_lock"]["path"])
    worker = ROOT / contract["probe"]["worker"]["path"]
    if sha(worker) != contract["probe"]["worker"]["sha256"]:
        raise ValueError("Frozen probe worker changed")
    _verify_probe_inputs(contract)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "entry_contract.json", contract)
    result = {
        "status": "BLOCKED",
        "task_id": task_id,
        "kind": "scripted_real_AppWorld_probe_not_model_evaluation",
        "model_requests": 0,
        "teacher_requests": 0,
        "gpu_requests": 0,
        "training_steps": 0,
        "implementation_sha256": sha(__file__),
    }
    try:
        with (output / "worker.log").open("x") as log:
            subprocess.run(
                [
                    sys.executable,
                    str(worker),
                    "worker",
                    "--root",
                    str(ROOT / contract["execution"]["appworld_root"]),
                    "--task",
                    task_id,
                    "--output",
                    str(output / "probe"),
                ],
                cwd=ROOT,
                check=True,
                timeout=contract["probe"]["timeout_seconds"],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        receipt = read_json(output / "probe/probe.json")
        result["worker_receipt"] = receipt
        official = output / "probe/official_evaluation.json"
        if (
            receipt.get("error_type")
            or receipt.get("evaluation_category") == "environment_error"
            or not receipt.get("read_only_state_unchanged")
            or not official.is_file()
        ):
            raise RuntimeError(
                "Probe chain did not produce complete execution evidence"
            )
        result.update(
            status="PASS",
            public_tool_calls=1,
            evaluation_status=receipt["evaluation_status"],
            official_evidence_sha256=sha(official),
            note="Probe connectivity only; raw unknown/failure is retained and no model success is claimed.",
        )
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        raise
    finally:
        write_json(output / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "probe"))
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    parser.add_argument("--task")
    parser.add_argument("--purpose", choices=tuple(PURPOSE_ROLE))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "inventory":
        result = inventory(args.protocol)
        write_json(args.output, result)
        print({"tasks": result["tasks"], "permissions": result["permission_counts"]})
    else:
        protocol, _, _, _ = load_contract(args.protocol)
        result = run_probe(
            args.task or protocol["probe"]["task_id"],
            args.purpose or protocol["probe"]["purpose"],
            args.output,
            args.protocol,
        )
        print({k: result[k] for k in ("status", "task_id", "model_requests")})


if __name__ == "__main__":
    main()
