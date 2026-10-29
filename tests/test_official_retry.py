"""Synthetic retry controller/credential tests; no model or private task content."""

import copy
from pathlib import Path

import pytest

from recoveragent import official_retry as retry
from recoveragent.io import files_manifest, read_json, write_json


def config():
    return read_json(retry.DEFAULT)


def test_second_credential_group_isolated_and_no_fallback(monkeypatch):
    value = config()
    credentials = {
        "DEEPSEEK_API_KEY": "synthetic-relay",
        "DEEPSEEK_BASE_URL": "https://znbcode.com/v1",
        "DEEPSEEK_MODEL": "relay-model",
        "DEEPSEEK_API_KEY1": "synthetic-official",
        "DEEPSEEK_BASE_URL1": "https://api.deepseek.com",
        "DEEPSEEK_MODEL1": "deepseek-flash",
    }
    monkeypatch.setattr(retry, "read_credentials", lambda _: credentials.copy())
    selected = retry.official_credentials(value, None)
    assert selected[value["teacher"]["api_key_env"]] == "synthetic-official"
    assert selected["DEEPSEEK_BASE_URL"] == "https://api.deepseek.com"
    assert credentials["DEEPSEEK_BASE_URL"] == "https://znbcode.com/v1"
    for field, wrong in (
        ("DEEPSEEK_API_KEY1", ""),
        ("DEEPSEEK_BASE_URL1", "https://api.deepseek.com@other.invalid"),
        ("DEEPSEEK_BASE_URL1", "https://znbcode.com/v1"),
        ("DEEPSEEK_MODEL1", "other-model"),
    ):
        monkeypatch.setattr(
            retry, "read_credentials", lambda _, f=field, w=wrong: {**credentials, f: w}
        )
        with pytest.raises(ValueError, match="second credential"):
            retry.official_credentials(value, None)


def synthetic_sources(monkeypatch):
    value = config()
    selection = value["retry_selection"]
    original = read_json(selection["source_report"])
    by_task = {r["task_id"]: r for r in original["rows"]}
    real_read = retry.read_json
    monkeypatch.setattr(retry, "verify_lock", lambda _: None)
    monkeypatch.setattr(
        retry,
        "read_json",
        lambda p: value
        if Path(p) == retry.DEFAULT
        else original
        if str(p) == selection["source_report"]
        else real_read(p),
    )

    def synthetic_sha(path):
        path = Path(path)
        if str(path) == selection["source_report"]:
            return selection["source_report_sha256"]
        if path.name == "campaign.json":
            return selection["source_campaign_sha256"]
        return by_task[path.parent.name]["review_sha256"]

    monkeypatch.setattr(retry, "sha256", synthetic_sha)
    monkeypatch.setattr(
        retry,
        "read_review",
        lambda p: {
            "original_status": by_task[p.name]["status"],
            "official_success": by_task[p.name]["official_success"],
            "eligible_for_sft": by_task[p.name]["eligible_for_sft"],
        },
    )
    return value, original


def test_exact_failed_unknown_selection_excludes_success_and_deferred(monkeypatch):
    value, original = synthetic_sources(monkeypatch)
    loaded, rows = retry.load_config(retry.DEFAULT)
    selected = {r["task_id"] for r in rows}
    assert len(rows) == 134 and loaded == value
    assert selected == {
        r["task_id"] for r in original["rows"] if r["status"] in {"FAILED", "UNKNOWN"}
    }
    assert not selected & {
        r["task_id"] for r in original["rows"] if r["official_success"] is True
    }
    value["retry_selection"]["tasks"].pop()
    with pytest.raises(ValueError, match="exactly"):
        retry.load_config(retry.DEFAULT)


def test_changed_source_or_key_scope_refuses_before_dispatch(monkeypatch):
    value, _ = synthetic_sources(monkeypatch)
    value["teacher"]["api_key_env"] = "DEEPSEEK_API_KEY"
    with pytest.raises(ValueError, match="scope/provider"):
        retry.load_config(retry.DEFAULT)
    monkeypatch.setattr(retry, "sha256", lambda _: "0" * 64)
    with pytest.raises(ValueError, match="selection report"):
        retry.load_config(retry.DEFAULT)


@pytest.mark.parametrize("fatal", [False, True])
def test_serial_paid_attempts_are_not_reissued_and_auth_stops(
    monkeypatch, tmp_path, fatal
):
    import os

    value = config()
    value.pop("official_continuation", None)
    _, all_rows = retry.a_tasks()
    allowed = {r["task_id"]: r for r in all_rows}
    rows = [allowed[t] for t in value["retry_selection"]["tasks"][:3]]
    monkeypatch.setattr(retry, "load_config", lambda _: (copy.deepcopy(value), rows))
    monkeypatch.setattr(
        retry,
        "official_credentials",
        lambda *a: {
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_MODEL": "deepseek-flash",
        },
    )
    monkeypatch.setattr(
        retry.fresh, "provider_preflight", lambda *a: {"status": "READY_FOR_PILOT"}
    )
    monkeypatch.setattr(retry.fresh, "environment_check", lambda: None)
    monkeypatch.setattr(retry, "authorize_task", lambda *a: None)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://synthetic-relay.invalid")
    launched = []

    def fake(worker, row, directory, identity, credentials, reviews):
        assert os.environ["DEEPSEEK_BASE_URL"] == "https://api.deepseek.com"
        assert worker["teacher"]["api_key_env"] == "DEEPSEEK_API_KEY1"
        launched.append(row["task_id"])
        record = {
            "original_status": "UNKNOWN" if fatal else "FAILED",
            "errors": ["provider_usage_unknown"]
            if fatal
            else ["official_task_failure"],
            "provider_http_status": 401 if fatal else None,
            "usage": {"requests": 1, "outputs": 10, "cost": 0.01, "unknown_cost": 0},
            "directory": str(directory),
            "raw_result_sha256": None,
            "trajectory_sha256": None,
        }
        home = reviews / row["task_id"]
        write_json(home / "review.json", record)
        write_json(home / "manifest.json", {"files": files_manifest(home)})
        return record

    monkeypatch.setattr(retry, "run_one", fake)
    retry.collect(retry.DEFAULT, tmp_path, None)
    assert len(launched) == (1 if fatal else 3)
    assert os.environ["DEEPSEEK_BASE_URL"] == "https://synthetic-relay.invalid"
    monkeypatch.setattr(
        retry, "run_one", lambda *a: pytest.fail("Duplicate paid attempt")
    )
    retry.collect(retry.DEFAULT, tmp_path, None)
    assert os.environ["DEEPSEEK_BASE_URL"] == "https://synthetic-relay.invalid"


def test_retry_export_completion_does_not_claim_a244(monkeypatch, tmp_path):
    value = config()
    value.pop("official_continuation", None)
    _, all_rows = retry.a_tasks()
    rows = [all_rows[0]]
    monkeypatch.setattr(retry, "load_config", lambda _: (value, rows))
    exporter = retry.fresh.script_helpers("export_fresh_parallel")
    root = tmp_path / "campaign"
    write_json(root / "campaign.json", value)
    (root / "campaign.lock").touch()
    record = {
        "directory": str(tmp_path / "missing_source"),
        "raw_result_sha256": None,
        "trajectory_sha256": None,
        "original_status": "UNKNOWN",
        "official_success": None,
        "eligible_for_sft": False,
        "errors": ["provider_usage_unknown"],
        "usage": {
            "requests": 1,
            "inputs": 0,
            "outputs": 0,
            "cached": 0,
            "cost": 0,
            "unknown_cost": 2.12992,
        },
        "completed_tool_results": 0,
    }
    home = root / "reviews" / rows[0]["task_id"]
    write_json(home / "review.json", record)
    write_json(home / "manifest.json", {"files": files_manifest(home)})
    destination = tmp_path / "bundle"
    public = exporter.export(retry.DEFAULT, root, destination, tmp_path / "public.json")
    manifest = read_json(destination / "manifest.json")
    assert manifest["complete_campaign"] is True
    assert manifest["complete_a244"] is False
    assert manifest["retry_selection"] == value["retry_selection"]
    assert public["summary"]["unknown_original_results"] == 1
    assert public["summary"]["official_failures"] == 0
    assert public["summary"]["trainable_candidates"] == 0


def test_continuation_keeps_sealed_unknown_and_refuses_changed_source(tmp_path):
    source = tmp_path / "previous"
    write_json(source / "campaign.json", {"synthetic": True})
    row = {
        "task_id": "synthetic_1",
        "scenario_id": "synthetic",
        "official_split": "train",
    }
    home = source / "reviews" / row["task_id"]
    record = {
        **row,
        "directory": str(tmp_path / "missing"),
        "raw_result_sha256": None,
        "trajectory_sha256": None,
        "original_status": "UNKNOWN",
    }
    write_json(home / "review.json", record)
    write_json(home / "manifest.json", {"files": files_manifest(home)})
    value = {
        "official_continuation": {
            "campaign": str(source),
            "campaign_sha256": retry.sha256(source / "campaign.json"),
            "reviews": {row["task_id"]: retry.sha256(home / "review.json")},
        }
    }
    target = tmp_path / "new"
    retry.inherit_reviews(value, [row], target)
    assert retry.read_review(target / "reviews" / row["task_id"]) == record
    retry.inherit_reviews(value, [row], target)
    (home / "review.json").write_text("{}\n")
    with pytest.raises(ValueError, match="review changed"):
        retry.inherit_reviews(value, [row], target)


def test_old_production_transport_lock_is_not_resigned():
    from recoveragent.protocol_lock import verify_lock
    from recoveragent.parallel_teacher import DEFAULT as old_default

    with pytest.raises(ValueError, match="teacher.py"):
        verify_lock(read_json(old_default))
    verify_lock(config())
