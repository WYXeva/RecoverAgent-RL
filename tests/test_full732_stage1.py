"""Admission tests use public metadata and synthetic receipts, never model calls."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from recoveragent import full732_stage1 as entry
from recoveragent.execution_v6 import SYSTEM_PROMPT, SYSTEM_PROMPT_SHA256
from recoveragent.io import digest, read_json, write_json
from recoveragent.unified_split import ROLES, sha


@pytest.fixture
def shadow(tmp_path, monkeypatch):
    """Copy only metadata for negative tests; no benchmark contents or weights."""
    protocol = read_json(entry.DEFAULT_PROTOCOL)
    for ref in (
        protocol[k]
        for k in (
            "split",
            "audit",
            "execution_reference",
            "runtime_lock",
            "clean_state_evidence",
        )
    ):
        target = tmp_path / ref["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry.ROOT / ref["path"], target)
    path = tmp_path / "protocol.json"
    shutil.copyfile(entry.DEFAULT_PROTOCOL, path)
    monkeypatch.setattr(entry, "ROOT", tmp_path)
    return path, protocol


def test_full_frozen_inventory_and_recovery_grpo_reuse():
    matrix = entry.inventory()
    assert (matrix["tasks"], matrix["scenarios"]) == (732, 244)
    assert matrix["scope"] == "full_pool_candidate"
    assert matrix["counts"] == dict.fromkeys(ROLES, 244)
    assert matrix["permission_counts"] == {
        "base_sft": {"allowed": 30, "blocked": 214},
        "recovery_grpo": {"allowed": 30, "blocked": 214},
        "shared_validation": {"allowed": 49, "blocked": 195},
    }
    by_purpose = {
        p: {r["task_id"] for r in entry.select_tasks(p)} for p in entry.PURPOSE_ROLE
    }
    assert by_purpose["recovery_sft"] == by_purpose["grpo"]
    a, b, c = (by_purpose[p] for p in ("base_sft", "recovery_sft", "validation"))
    assert not (a & b or a & c or b & c)
    assert len(a | b | c) == 732
    for scenario in {r["scenario_id"] for r in matrix["rows"]}:
        assert {
            r["role"] for r in matrix["rows"] if r["scenario_id"] == scenario
        } == set(ROLES)
    assert all(r["metadata_eligible"] for r in matrix["rows"])
    assert all(
        not r["execution_ready"] and not r["dispatch_allowed"] for r in matrix["rows"]
    )


@pytest.mark.parametrize(
    "fault", ["status", "purpose", "split", "seed", "delete", "train_only"]
)
def test_edited_or_self_rehashed_manifest_cannot_replace_frozen_split(shadow, fault):
    path, protocol = shadow
    p = entry.ROOT / protocol["split"]["path"]
    plan = read_json(p)
    if fault == "status":
        plan.update(status="READY", training_authorized=True)
        plan["rows"][0]["permission_status"] = "READY"
    elif fault == "purpose":
        plan["rows"][0]["role"] = "shared_validation"
    elif fault == "split":
        plan["rows"][0]["official_split"] = "train"
    elif fault == "seed":
        plan["seed"] += 1
    elif fault == "delete":
        plan["rows"].pop()
    else:
        plan["scope"] = "official_train"
    plan["manifest_sha256"] = digest(
        {k: v for k, v in plan.items() if k != "manifest_sha256"}
    )
    p.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="Frozen source changed"):
        entry.inventory(path)


@pytest.mark.parametrize(
    "fault", ["missing_policy", "fake_grant", "ready", "new_hash", "wrong_probe"]
)
def test_config_switches_or_rebound_hash_do_not_grant_permission(shadow, fault):
    path, protocol = shadow
    if fault == "missing_policy":
        del protocol["upstream_policy"]
    elif fault == "fake_grant":
        protocol["upstream_policy"]["additional_grants"] = [
            {"approved": True, "split": "test_normal"}
        ]
        protocol["upstream_policy"]["grants"]["parameter_training"].append(
            "test_normal"
        )
    elif fault == "ready":
        protocol.update(
            usage_authorized=True, execution_ready=True, model_execution_authorized=True
        )
    elif fault == "new_hash":
        protocol["split"]["sha256"] = "0" * 64
    else:
        protocol["probe"]["task_id"] = "024c982_3"
    path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="Unreviewed or changed"):
        entry.inventory(path)


@pytest.mark.parametrize("purpose", list(entry.PURPOSE_ROLE))
@pytest.mark.parametrize("split", ["test_normal", "test_challenge"])
def test_test_task_dispatch_blocked_before_data_or_worker_access(
    monkeypatch, tmp_path, split, purpose
):
    task = next(r for r in entry.select_tasks(purpose) if r["official_split"] == split)
    # File presence does not confer authorization; neither reader may be reached.
    (tmp_path / task["task_id"]).mkdir()

    def forbidden(*args, **kwargs):
        pytest.fail("Restricted task reached runtime/data access")

    monkeypatch.setattr(entry, "_verify_probe_inputs", forbidden)
    monkeypatch.setattr(entry.subprocess, "run", forbidden)
    out = tmp_path / "denied"
    with pytest.raises(PermissionError, match="upstream_split_usage_restricted"):
        entry.run_probe(task["task_id"], purpose, out)
    assert not out.exists()


@pytest.mark.parametrize("purpose", ["base_sft", "recovery_sft", "grpo"])
def test_dev_training_denied(purpose):
    task = next(r for r in entry.select_tasks(purpose) if r["official_split"] == "dev")
    with pytest.raises(PermissionError, match="upstream_split_usage_restricted"):
        entry.probe_contract(task["task_id"], purpose)


def test_allowed_usage_is_not_blanket_stage_or_probe_authorization():
    protocol, plan, _, _ = entry.load_contract()
    fixed = protocol["probe"]["task_id"]
    assert fixed == min(
        r["task_id"] for r in plan["rows"] if r["official_split"] == "train"
    )
    for purpose in ("base_sft", "validation"):
        with pytest.raises(ValueError, match="different A/B/C"):
            entry.probe_contract(fixed, purpose)
    for split in ("train", "dev"):
        task = next(
            r for r in entry.select_tasks("validation") if r["official_split"] == split
        )
        with pytest.raises(PermissionError, match="only the fixed"):
            entry.probe_contract(task["task_id"], "validation")
    for invalid in ("M1", "stage2", "training", "teacher", "inference"):
        with pytest.raises(ValueError, match="Unknown data purpose"):
            entry.probe_contract(fixed, invalid)


def test_report_status_edit_is_not_dispatch_credential():
    report = entry.inventory()
    row = next(r for r in report["rows"] if not r["usage_authorized"])
    row.update(usage_authorized=True, execution_ready=True, dispatch_allowed=True)
    with pytest.raises(PermissionError):
        entry.probe_contract(row["task_id"], row["purposes"][0])


def test_prompt_and_future_clean_state_contract_are_separate_from_control():
    protocol, _, reference, _ = entry.load_contract()
    contract = entry.probe_contract(protocol["probe"]["task_id"], "recovery_sft")
    assert contract["system_message"] == SYSTEM_PROMPT
    assert hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() == SYSTEM_PROMPT_SHA256
    assert (
        sha(entry.ROOT / "src/recoveragent/execution_v6.py")
        == "352d106b7b052cc1a2b5c249e19c6a512e989355fc60f6495034f32db24a4400"
    )
    assert (
        contract["start_kind"] == "clean_original_task"
        and contract["resume_checkpoint"] is None
    )
    assert contract["execution"] == reference["execution"]
    assert contract["actor_request_sent"] is False
    assert "Stage 1" not in contract["system_message"]


def test_initial_unknown_neither_changes_permission_nor_claims_terminal_impossibility():
    rows = entry.inventory()["rows"]
    unknown = [r for r in rows if r["clean_initial_evaluation"] == "evaluation_error"]
    assert len(unknown) == 6
    assert all(r["usage_authorized"] for r in unknown)
    assert all(r["successful_terminal_scoring"] == "NOT_VERIFIED" for r in unknown)
    assert all(not r["execution_ready"] for r in rows)


@pytest.mark.parametrize("evaluation_status", ["valid", "evaluation_error"])
def test_synthetic_worker_wiring_retains_unknown_and_provenance(
    monkeypatch, tmp_path, evaluation_status
):
    protocol, _, _, _ = entry.load_contract()
    monkeypatch.setattr(entry, "_verify_probe_inputs", lambda _: None)
    calls = []

    def worker(command, **kwargs):
        calls.append(command)
        target = Path(command[-1])
        write_json(
            target / "probe.json",
            {
                "task_id": protocol["probe"]["task_id"],
                "read_only_state_unchanged": True,
                "evaluation_status": evaluation_status,
                "synthetic": True,
            },
        )
        write_json(
            target / "official_evaluation.json",
            {"status": evaluation_status, "success": None, "synthetic": True},
        )

    monkeypatch.setattr(entry.subprocess, "run", worker)
    out = tmp_path / "synthetic"
    result = entry.run_probe(protocol["probe"]["task_id"], "recovery_sft", out)
    assert len(calls) == 1 and calls[0][2] == "worker"
    assert (
        result["status"] == "PASS" and result["evaluation_status"] == evaluation_status
    )
    assert result["model_requests"] == 0
    assert read_json(out / "entry_contract.json")["system_message"] == SYSTEM_PROMPT
    assert read_json(out / "probe/official_evaluation.json")["success"] is None
    with pytest.raises(FileExistsError):
        entry.run_probe(protocol["probe"]["task_id"], "recovery_sft", out)
    assert len(calls) == 1  # No overwrite/retry.


def test_worker_fault_preserves_receipt_without_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(entry, "_verify_probe_inputs", lambda _: None)
    calls = []

    def timeout(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(entry.subprocess, "run", timeout)
    protocol, _, _, _ = entry.load_contract()
    out = tmp_path / "interrupted"
    with pytest.raises(subprocess.TimeoutExpired):
        entry.run_probe(protocol["probe"]["task_id"], "recovery_sft", out)
    assert len(calls) == 1
    assert read_json(out / "result.json")["status"] == "BLOCKED"
    assert (out / "entry_contract.json").is_file()


def test_evaluation_environment_error_does_not_pass_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(entry, "_verify_probe_inputs", lambda _: None)

    def worker(command, **kwargs):
        target = Path(command[-1])
        write_json(
            target / "probe.json",
            {
                "read_only_state_unchanged": True,
                "evaluation_status": "evaluation_error",
                "evaluation_category": "environment_error",
            },
        )
        write_json(
            target / "official_evaluation.json", {"success": None, "synthetic": True}
        )

    monkeypatch.setattr(entry.subprocess, "run", worker)
    protocol, _, _, _ = entry.load_contract()
    out = tmp_path / "evaluation_failure"
    with pytest.raises(RuntimeError, match="complete execution evidence"):
        entry.run_probe(protocol["probe"]["task_id"], "recovery_sft", out)
    assert read_json(out / "result.json")["status"] == "BLOCKED"


def test_existing_student_v6_guard_incompatibility_is_reported_not_bypassed(
    monkeypatch,
):
    from recoveragent import student
    from test_student_batch import Adapter, DISCOVER, Tokenizer

    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )
    with pytest.raises(ValueError, match="harness must be declared"):
        student.StudentSession(
            Adapter(),
            Tokenizer(),
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "synthetic"},
            ],
            [DISCOVER],
            tool_mode="dynamic_discovery",
            protocol_harness=True,
            answer_guard=True,
        )
