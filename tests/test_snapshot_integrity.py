"""Four focused software regressions, not claims of real task performance."""

import copy
import importlib.util

import pytest

from recoveragent.io import files_manifest, read_json, write_json


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location(
        "snapshot_probe", "scripts/probe_snapshot_integrity.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def paired(probe, *, write=True):
    metadata = probe.metadata_summary({"synthetic": {"Object": 1}} if write else {})
    state = {
        "metadata": metadata,
        "db_fingerprint": "rows",
        "changes_sha256": "changes",
        "changes_counts": {"synthetic": int(write)},
    }
    disk = {"metadata": metadata, "files": {"model_hashes.json": "file_sha"}}
    evaluation = {
        "status": "valid",
        "official_result_complete": True,
        "official": {"success": True, "num_tests": 1, "passes": [{}], "failures": []},
    }
    live = {
        "pid": 1,
        "before_save": state,
        "after_save": state,
        "saved": disk,
        "exported": disk,
        "evaluation": evaluation,
        "write_observed": write,
        "receipt_sha256": "trusted_parent",
    }
    restored = {
        "pid": 2,
        "after_import": state,
        "saved_terminal": disk,
        "evaluation": evaluation,
        "export_unchanged": True,
        "evaluation_db_unchanged": True,
        "live_receipt_sha256": "trusted_parent",
    }
    return copy.deepcopy(live), copy.deepcopy(restored)


def test_write_requires_metadata_tracking_and_equal_complete_scores(probe):
    live, restored = paired(probe)
    assert probe.integrity_verdict(live, restored)["status"] == "PASS"
    for field, bad in [
        ("metadata", probe.metadata_summary({})),
        ("changes_sha256", "changed"),
        ("db_fingerprint", "changed"),
    ]:
        altered = copy.deepcopy(restored)
        altered["after_import"][field] = bad
        assert probe.integrity_verdict(live, altered)["status"] == "FAIL"
    altered = copy.deepcopy(restored)
    altered["evaluation"]["official"]["success"] = False
    assert probe.integrity_verdict(live, altered)["status"] == "FAIL"
    altered["evaluation"]["official_result_complete"] = False
    assert probe.integrity_verdict(live, altered)["status"] == "FAIL"
    assert probe.integrity_verdict(live, {})["status"] == "NOT_VERIFIED"


def test_empty_metadata_readonly_exception_not_general_repair(probe):
    live, restored = paired(probe, write=False)
    assert probe.integrity_verdict(live, restored)["status"] == "PASS"
    live["write_observed"] = True
    result = probe.integrity_verdict(live, restored)
    assert result["status"] == "FAIL"
    assert result["checks"]["write_metadata_present"] is False


def test_tampered_export_rejected_before_environment_load(probe, tmp_path, monkeypatch):
    source = tmp_path / "live"
    export = source / "export"
    write_json(export / "model_hashes.json", {"synthetic": {"Object": 1}})
    files = files_manifest(export)
    write_json(
        source / "receipt.json",
        {
            "task_id": probe.TASK,
            "evaluation": {"success": True},
            "exported": {"files": files},
        },
    )
    trusted = probe.sha256(source / "receipt.json")
    (export / "model_hashes.json").write_text("{}")
    monkeypatch.setattr(
        probe, "environment_check", lambda: pytest.fail("No environment")
    )
    with pytest.raises(ValueError, match="live process receipt"):
        probe.restore_probe(tmp_path, tmp_path / "out", source, trusted)
    with pytest.raises(ValueError, match="Untrusted live receipt"):
        probe.restore_probe(tmp_path, tmp_path / "out", source, "forged")


def test_failure_keeps_partial_evidence_and_refuses_overwrite(probe, tmp_path):
    output = tmp_path / "failed"

    def interrupted():
        write_json(output / "boundaries.json", {"actual_side_effect": "preserved"})
        raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError):
        probe.run_recorded(output, interrupted)
    assert read_json(output / "receipt.json")["execution_status"] == "error"
    assert read_json(output / "boundaries.json")["actual_side_effect"] == "preserved"
    sealed = read_json(output / "manifest.json")["files"]
    assert "receipt.json" in sealed and "boundaries.json" in sealed
    with pytest.raises(FileExistsError):
        probe.run_recorded(output, lambda: {})
    assert read_json(output / "manifest.json")["files"] == sealed
