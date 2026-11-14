"""Quota pauses preserve task claims and original source evidence."""

import copy

import pytest

from recoveragent import teacher_followup as followup, teacher_retry as retry
from recoveragent.demonstrations import sha256
from recoveragent.io import read_json, write_json
from recoveragent.teacher import http_error_diagnostic


def test_retry_after_is_numeric_only_and_body_is_not_disclosed():
    body = b'{"error":{"type":"GoUsageLimitError","message":"private-account-link"}}'
    result = http_error_diagnostic(body, "5002")
    assert result["retry_after_seconds"] == 5002
    assert result["go_usage_limit"]
    assert "private-account" not in str(result)
    for invalid in (None, "Wed, 21 Oct 2026", "private", "-5", "999999999999"):
        assert "retry_after_seconds" not in http_error_diagnostic(body, invalid)


def test_selection_excludes_success_from_either_campaign_and_preserves_sources(
    tmp_path, monkeypatch
):
    cfg = copy.deepcopy(followup.configuration(retry.REVISION))
    tasks = followup.configuration(followup.LOCAL_ESCALATION)["task_ids"][:3]
    previous, pro, output = (tmp_path / x for x in ("previous", "pro", "retry"))
    for root in (previous, pro):
        write_json(root / "config.json", {})
        write_json(root / "progress.json", {"status": "complete"})
    write_json(previous / "campaign.json", {})
    write_json(
        pro / "campaign.json",
        {
            "revision": followup.LOCAL_ESCALATION,
            "source_lock_sha256": "prior-lock",
            "task_ids": tasks[1:],
        },
    )
    rows = []
    for i, task in enumerate(tasks):
        path = previous / "results" / (task + ".json")
        row = {"task_id": task, "eligible": i == 0}
        write_json(path, row)
        rows.append((path, row))
    for i, task in enumerate(tasks[1:]):
        entry = {"task_id": task, "teacher": "deepseek_pro", "eligible": i == 1}
        write_json(
            pro / "results" / (task + ".json"),
            {"task_id": task, "eligible": i == 1, "attempts": [entry]},
        )
    cfg["task_ids"] = [tasks[1]]
    cfg["retry_source"].update(
        config_sha256=sha256(pro / "config.json"), source_lock_sha256="prior-lock"
    )
    monkeypatch.setattr(retry, "configuration", lambda: cfg)
    monkeypatch.setattr(retry.escalation, "prior_results", lambda *a: rows)
    monkeypatch.setattr(followup, "verify_lock", lambda *a: "new-lock")
    retry.prepare(output, {"previous_output": str(previous), "pro_output": str(pro)})
    assert read_json(output / "campaign.json")["task_ids"] == [tasks[1]]
    retry.escalation.verify_source_results(output)
    (pro / "results" / (tasks[2] + ".json")).write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        retry.escalation.verify_source_results(output)


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    cfg = copy.deepcopy(followup.configuration(retry.REVISION))
    cfg["task_ids"] = cfg["task_ids"][:2]
    clock = [1000.0]
    output = tmp_path / "retry"
    write_json(output / "config.json", cfg)
    write_json(output / "deployment.json", {"deadline_epoch": 10000})
    write_json(
        output / "campaign.json",
        {
            "revision": retry.REVISION,
            "source_lock_sha256": "synthetic",
            "task_ids": cfg["task_ids"],
            "carried_attempts": {},
            "source_results": {},
        },
    )
    monkeypatch.setattr(retry, "configuration", lambda: cfg)
    monkeypatch.setattr(followup, "verify_lock", lambda *a: "synthetic")
    monkeypatch.setattr(followup, "configuration", lambda *a: cfg)
    monkeypatch.setattr(retry.time, "time", lambda: clock[0])
    return output, cfg, clock


def test_task_429_pauses_before_next_claim_then_resumes_without_replay(
    campaign, monkeypatch
):
    output, cfg, clock = campaign
    invoked, probes = [], []
    monkeypatch.setattr(retry, "availability", lambda *a: probes.append(True))
    monkeypatch.setattr(
        followup, "export", lambda o: write_json(o / "export/manifest.json", {})
    )

    def run_task(out, task, deployment):
        invoked.append(task)
        entry = {"task_id": task, "teacher": "deepseek_pro", "eligible": False}
        result = {"task_id": task, "eligible": False, "attempts": [entry]}
        write_json(out / "results" / (task + ".json"), result)
        error = (
            {
                "provider_error": {
                    "http_status": 429,
                    "diagnostic": {"retry_after_seconds": 100},
                }
            }
            if task == cfg["task_ids"][0]
            else {}
        )
        write_json(
            out / "attempts" / f"{out.name}_{task}_deepseek_pro" / "trajectory.json",
            error,
        )
        return result

    monkeypatch.setattr(followup, "run_task", run_task)
    assert retry.collect(output)["status"] == "waiting_for_service"
    assert invoked == cfg["task_ids"][:1]
    assert read_json(output / "service_pause.json")["retry_at_epoch"] == 1160
    assert retry.collect(output)["status"] == "waiting_for_service"
    assert len(probes) == 1 and len(invoked) == 1
    clock[0] = 1161
    assert retry.collect(output)["status"] == "complete"
    assert invoked == cfg["task_ids"] and len(probes) == 2
    assert retry.collect(output)["status"] == "complete"
    assert len(probes) == 2


@pytest.mark.parametrize("status", [429, 401])
def test_service_probe_failure_never_claims_a_task(campaign, monkeypatch, status):
    output, cfg, clock = campaign
    monkeypatch.setattr(retry, "availability", lambda *a: {"http_status": status})
    monkeypatch.setattr(followup, "run_task", lambda *a: pytest.fail("must not claim"))
    result = retry.collect(output)
    assert result["status"] == (
        "waiting_for_service" if status == 429 else "service_blocked"
    )
    assert not (output / "attempts").exists()


def test_initial_provider_reset_and_deadline_prevent_paid_probe(campaign, monkeypatch):
    output, cfg, clock = campaign
    monkeypatch.setattr(retry, "availability", lambda *a: pytest.fail("must not probe"))
    retry.pause(output, 1200, "initial_retry_after")
    assert retry.collect(output)["status"] == "waiting_for_service"
    clock[0] = 9900
    assert retry.collect(output)["status"] == "partial"


def test_worker_uses_new_budget_explicitly_without_changing_context(monkeypatch):
    from recoveragent import p3a

    cfg = copy.deepcopy(followup.configuration(retry.REVISION))
    cfg["max_generated_tokens"] = 32768
    monkeypatch.setattr(followup, "configuration", lambda *a: cfg)
    worker = followup.worker_config(
        cfg["task_ids"][0],
        "synthetic",
        "/synthetic",
        "deepseek_pro",
        revision=retry.REVISION,
    )
    assert (
        worker["remaining_generated_tokens"]
        == worker["limits"]["episode_generated_tokens"]
        == worker["limits"]["total_generated_tokens"]
        == 32768
    )
    assert worker["collection_context"]["max_sequence_tokens"] == 65536
    assert (
        worker["remaining_requests"] == 80
        and worker["limits"]["episode_wall_seconds"] == 1200
    )
    monkeypatch.setattr(followup, "verify_lock", lambda *a: "synthetic")
    p3a.validate_config(worker)
    old = copy.deepcopy(worker)
    old["v2_runtime"] = followup.LOCAL_ESCALATION
    monkeypatch.setattr(followup, "validate_worker_config", lambda *a: None)
    with pytest.raises(ValueError, match="episode_generated_tokens"):
        p3a.validate_config(old)


def test_retry_lock_does_not_resign_previous_pro_run():
    assert len(followup.verify_lock(retry.REVISION)) == 64
    with pytest.raises(ValueError, match="source|Incomplete"):
        followup.verify_lock(followup.LOCAL_ESCALATION)
