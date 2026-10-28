"""Fresh custom research scope/budget/receipts; no real model or historical data."""

import copy
from collections import Counter
from pathlib import Path

import pytest

from recoveragent import fresh_teacher as fresh, research_access as access
from recoveragent.demonstrations import validate_demo
from recoveragent.io import digest, files_manifest, write_json
from recoveragent.teacher import preflight


def test_frozen_a244_provenance_and_no_repartition():
    plan, rows = access.a_tasks()
    assert (
        len(rows)
        == len({r["task_id"] for r in rows})
        == len({r["scenario_id"] for r in rows})
        == 244
    )
    assert Counter(r["official_split"] for r in rows) == {
        "train": 30,
        "dev": 19,
        "test_normal": 56,
        "test_challenge": 139,
    }
    assert not {r["task_id"] for r in rows} & {
        r["task_id"] for r in plan["rows"] if r["role"] != "base_sft"
    }
    assert all(
        access.validate_identity(r["task_id"], access.context_for(r)) == r for r in rows
    )


def test_scope_cannot_authorize_b_c_or_redistribution_or_relabel():
    plan, rows = access.a_tasks()
    row = rows[0]
    for change in (
        {"official_split": "train"},
        {"public_redistribution": True},
        {"official_benchmark_claim": True},
        {"role": "recovery_grpo"},
        {"permission_status": "READY"},
    ):
        with pytest.raises(ValueError):
            access.validate_identity(
                row["task_id"], {**access.context_for(row), **change}
            )
    for excluded in (r for r in plan["rows"] if r["role"] != "base_sft"):
        with pytest.raises(ValueError):
            access.validate_identity(excluded["task_id"], access.context_for(row))


def test_installed_license_and_membership_required(tmp_path):
    _, rows = access.a_tasks()
    row = rows[0]
    assert (
        access.authorize_task("data/appworld", row["task_id"], access.context_for(row))
        == row["official_split"]
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data/LICENSE").write_text("pretend all permissions granted")
    with pytest.raises(ValueError, match="terms"):
        access.authorize_task(tmp_path, row["task_id"], access.context_for(row))


def test_schedule_budget_and_smoke_are_fixed():
    config, rows = fresh.campaign_config()
    assert rows[0]["task_id"] == "07b42fd_2"
    assert config["fresh_campaign"]["total_cost_cap"] == 244
    assert config["fresh_campaign"]["max_attempts_per_task"] == 1
    worker = fresh.worker_config(
        config, rows[0], {"cost": 243.5, "requests": 9740, "outputs": 3997690}
    )
    assert worker["remaining_cost"] == 0.5
    assert (
        worker["remaining_requests"] == 20 and worker["remaining_generated_tokens"] == 6
    )
    missing = copy.deepcopy(config)
    missing["authorization"]["max_cost"] = None
    assert (
        "COST_LIMIT_REQUIRED"
        in preflight(missing, {"DEEPSEEK_API_KEY": "synthetic"})["reasons"]
    )


def test_claim_survives_interruption_and_cannot_be_counted_as_success(tmp_path):
    config, rows = fresh.campaign_config()
    before = fresh.result_rows(config, rows, tmp_path)
    assert len(before) == 244 and all(r["status"] == "NOT_RUN" for r in before)
    identity = before[0]["attempt_id"]
    directory = tmp_path / "attempts" / ("fresh_" + identity[:20])
    write_json(directory.with_suffix(".claim.json"), {"attempt_id": identity})
    after = fresh.result_rows(config, rows, tmp_path)
    assert after[0]["status"] == "INTERRUPTED" and after[0]["official_success"] is None
    assert after[0]["cost_or_unknown"] is None and not after[0]["eligible_for_sft"]
    assert fresh.totals(after)["cost"] == 1
    assert all(r["attempt_count"] == 0 for r in after[1:])


def test_completed_attempt_integrity_before_resuming(tmp_path):
    config, rows = fresh.campaign_config()
    identity = digest([digest(config), rows[0]["task_id"], 1])
    directory = tmp_path / "attempts" / ("fresh_" + identity[:20])
    write_json(directory / "result.json", {"attempt_id": identity, "status": "FAILED"})
    write_json(
        directory / "evidence_manifest.json", {"files": files_manifest(directory)}
    )
    assert fresh.result_rows(config, rows, tmp_path)[0]["status"] == "FAILED"
    (directory / "result.json").write_text('{"status":"SUCCEEDED"}')
    with pytest.raises(ValueError, match="evidence changed"):
        fresh.result_rows(config, rows, tmp_path)


def test_interruption_keeps_known_usage_and_remaining_reservation(
    monkeypatch, tmp_path
):
    config, rows = fresh.campaign_config()
    identity = digest([digest(config), rows[0]["task_id"], 1])
    directory = tmp_path / "attempts" / ("fresh_" + identity[:20])
    directory.mkdir(parents=True)
    write_json(directory.with_suffix(".claim.json"), {"attempt_id": identity})
    monkeypatch.setattr(
        fresh,
        "interrupted_usage",
        lambda *a: dict(
            prompt_tokens=100,
            generated_tokens=20,
            provider_requests=2,
            tool_calls=3,
            usage_priced_cost=0.2,
        ),
    )
    records = fresh.result_rows(config, rows, tmp_path)
    assert records[0]["model_requests"] == 2
    assert records[0]["input_tokens"] == 100
    assert records[0]["output_tokens"] == 20
    assert records[0]["tool_calls"] == 3
    assert records[0]["unknown_cost_reservation"] == 0.8
    assert records[0]["cost_or_unknown"] is None
    assert fresh.totals(records)["cost"] == 1


def test_official_filters_do_not_accept_relabeled_custom_data():
    _, rows = access.a_tasks()
    from recoveragent.execution_v6 import SYSTEM_PROMPT

    record = {
        "task_id": rows[0]["task_id"],
        "scenario_id": rows[0]["scenario_id"],
        "split": "train",
        "custom_research": access.context_for(rows[0]),
        "kind": access.FORMAT,
        "actor_prompt_variant": "execution_v6",
        "tool_mode": "dynamic_discovery",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
    }
    with pytest.raises(ValueError, match="relabels"):
        validate_demo(record)


def test_no_provider_call_without_authorization(monkeypatch, tmp_path):
    config, rows = fresh.campaign_config()
    config["authorization"]["approved"] = False
    monkeypatch.setattr(fresh, "campaign_config", lambda _: (config, rows))
    monkeypatch.setattr(
        fresh, "run_child", lambda *a, **k: pytest.fail("Paid dispatch")
    )
    with pytest.raises(ValueError, match="preflight blocked"):
        fresh.collect(Path("synthetic"), tmp_path, None, 25)


def test_resume_never_reissues_claimed_request(monkeypatch, tmp_path):
    config, rows = fresh.campaign_config()
    monkeypatch.setattr(
        fresh, "read_credentials", lambda _: {"DEEPSEEK_API_KEY": "synthetic"}
    )
    monkeypatch.setattr(fresh, "environment_check", lambda: {})
    monkeypatch.setattr(
        fresh,
        "provider_preflight",
        lambda *a: {"status": "READY_FOR_PILOT", "reasons": []},
    )
    monkeypatch.setattr(
        fresh, "run_child", lambda *a, **k: pytest.fail("Duplicate paid dispatch")
    )
    identity = digest([digest(config), rows[0]["task_id"], 1])
    write_json(
        tmp_path / "attempts" / ("fresh_" + identity[:20] + ".claim.json"),
        {"attempt_id": identity},
    )
    result = fresh.collect(fresh.DEFAULT, tmp_path, None, 25)
    assert result[0]["status"] == "INTERRUPTED"


def test_source_lock_rejects_changed_execution_parameters():
    config, _ = fresh.campaign_config()
    config["teacher"]["max_new_tokens"] = 8192
    with pytest.raises(ValueError):
        fresh.verify_lock(config)


def test_custom_worker_reuses_real_checks_and_preserves_original_split(
    monkeypatch, tmp_path, current_teacher_unit_lock
):
    from recoveragent import APPWORLD_REVISION, p3a
    from recoveragent.teacher import TeacherPolicy
    from recoveragent.demonstrations import as_demo, validate_receipts
    from test_runner import Adapter
    from test_teacher_data import response

    config, _ = fresh.campaign_config()
    config["teacher"]["pricing"].update(
        input_per_million=0.3,
        cached_input_per_million=0.006,
        output_per_million=1.2,
        source="SYNTHETIC_TEST_ONLY",
        checked_at="SYNTHETIC_TEST_ONLY",
    )
    config["fresh_campaign"]["provider_access_verified"] = True
    config["teacher"].update(
        base_url="https://synthetic.invalid/v1", allowed_host="synthetic.invalid"
    )
    config = current_teacher_unit_lock(config)
    _, rows = access.a_tasks()
    row = rows[0]  # Test-origin ID, all content and responses here are synthetic.
    worker = fresh.worker_config(config, row, {"cost": 0, "requests": 0, "outputs": 0})

    class FakeWorld(Adapter):
        versions = {"revision": APPWORLD_REVISION, "data_version": "SYNTHETIC"}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.official_split = row["official_split"]
            self.world.task.generator_id = row["scenario_id"]

    monkeypatch.setattr(fresh, "FreshAdapter", FakeWorld)
    monkeypatch.setattr(
        "recoveragent.evaluation.environment_check", lambda: {"synthetic": True}
    )
    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda cfg, creds: TeacherPolicy(cfg, creds, transport=lambda _: response()),
    )
    monkeypatch.setattr(
        fresh,
        "evaluate_custom_isolated",
        lambda *a: {
            "status": "valid",
            "success": True,
            "official": {"synthetic": True},
        },
    )
    output = tmp_path / "synthetic_worker"
    trajectory = p3a.run_teacher_episode(
        worker, row["task_id"], output, {"DEEPSEEK_API_KEY": "synthetic"}
    )
    assert trajectory["status"] == "valid" and trajectory["split"] == "test_normal"
    assert trajectory["custom_research"] == worker["custom_research"]
    record = as_demo(trajectory)
    assert record["kind"] == access.FORMAT
    validate_demo(record)
    validate_receipts(trajectory, output)
    for key in ("answer_guard", "harness"):
        broken = copy.deepcopy(record)
        broken.pop(key)
        with pytest.raises(ValueError, match="actual declared controls"):
            validate_demo(broken)


def test_relay_variables_cannot_silently_use_official_endpoint_or_prices():
    config, _ = fresh.campaign_config()
    credentials = {
        "DEEPSEEK_API_KEY": "synthetic",
        "DEEPSEEK_BASE_URL": "https://znbcode.com/v1",
        "DEEPSEEK_MODEL": "deepseek-flash",
    }
    check = fresh.provider_preflight(config, credentials)
    assert check["status"] == "BLOCKED"
    assert "VERIFIED_PRICE_REQUIRED" in check["reasons"]
    assert "PROVIDER_ACCESS_NOT_VERIFIED" in check["reasons"]
    assert "PROVIDER_TERMS_DISALLOW_TRAINING_DATA" in check["reasons"]
    wrong = copy.deepcopy(config)
    wrong["teacher"]["base_url"] = "https://api.deepseek.com"
    assert (
        "CREDENTIAL_ENDPOINT_CONFIG_MISMATCH"
        in fresh.provider_preflight(wrong, credentials)["reasons"]
    )
    credentials["DEEPSEEK_MODEL"] = "other_model"
    assert (
        "CREDENTIAL_MODEL_CONFIG_MISMATCH"
        in fresh.provider_preflight(config, credentials)["reasons"]
    )


def test_provider_use_restriction_is_not_overridden_by_ready_flag():
    config, _ = fresh.campaign_config()
    config["fresh_campaign"]["provider_access_verified"] = True
    config["teacher"]["pricing"].update(
        input_per_million=1,
        cached_input_per_million=1,
        output_per_million=1,
        source="SYNTHETIC_TEST_ONLY",
        checked_at="SYNTHETIC_TEST_ONLY",
    )
    check = fresh.provider_preflight(config, {"DEEPSEEK_API_KEY": "synthetic"})
    assert check["status"] == "BLOCKED"
    assert check["reasons"] == ["PROVIDER_TERMS_DISALLOW_TRAINING_DATA"]
