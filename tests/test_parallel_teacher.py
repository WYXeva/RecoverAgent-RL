"""Controller-only simulations; no Teacher calls, task content or model weights."""

import copy
from pathlib import Path
import threading
import time

import pytest

from recoveragent import fresh_teacher as fresh, parallel_teacher as parallel
from recoveragent.io import files_manifest, read_json, write_json


def test_clean_training_length_already_excludes_provider_reasoning():
    from recoveragent.teacher import normalize_message
    from test_teacher_data import response

    raw = response()
    raw["choices"][0]["message"]["reasoning_content"] = "audit only " * 10000
    message = normalize_message(raw, allow_reasoning=True)
    assert "reasoning_content" not in message
    assert "audit only" not in str(message)
    assert message["tool_calls"] == raw["choices"][0]["message"]["tool_calls"]


def test_frozen_parallel_scope_and_prior_source_receipts(tmp_path):
    from recoveragent.demonstrations import sha256
    from recoveragent.io import digest

    config, rows = fresh.campaign_config(parallel.DEFAULT)
    assert len(config["parallel_collection"]["prior"]["results"]) == 5
    config["parallel_collection"].pop("continuation_reviews", None)
    # Synthetic prior receipts: unit tests do not require private Teacher runs.
    write_json(tmp_path / "campaign.json", config)
    task = rows[0]["task_id"]
    identity = digest([digest(config), task, 1])
    directory = tmp_path / "attempts" / ("fresh_" + identity[:20])
    write_json(
        directory / "result.json",
        {"attempt_id": identity, "attempt_count": 1, "status": "FAILED"},
    )
    write_json(
        directory / "evidence_manifest.json", {"files": files_manifest(directory)}
    )
    config["parallel_collection"]["prior"] = {
        "output": str(tmp_path),
        "campaign_sha256": sha256(tmp_path / "campaign.json"),
        "results": {task: sha256(directory / "result.json")},
    }
    inherited = parallel.inherited_records(config, rows)
    assert len(rows) == 244 and len(inherited) == 1
    assert config["parallel_collection"]["workers"] == 1
    assert config["limits"]["episode_requests"] == 80
    broken = copy.deepcopy(config)
    broken["parallel_collection"]["prior"]["campaign_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Prior campaign"):
        parallel.inherited_records(broken, rows)


def test_over_limit_receipt_is_costed_without_rewriting_original(tmp_path):
    config, _ = fresh.campaign_config(parallel.DEFAULT)
    path = tmp_path / "requests"
    write_json(path / "001.reservation.json", {"max_tokens": 507, "max_cost": 0.4})
    write_json(
        path / "001.response.json",
        {
            "id": "synthetic",
            "model": config["teacher"]["model"],
            "usage": {"prompt_tokens": 100, "completion_tokens": 512},
        },
    )
    before = files_manifest(tmp_path)
    usage, errors = parallel.receipt_usage(tmp_path, config["teacher"])
    assert usage["outputs"] == 512 and usage["unknown_cost"] == 0
    assert usage["cost"] > 0 and errors == ["provider_token_bound"]
    assert files_manifest(tmp_path) == before
    write_json(path / "002.reservation.json", {"max_tokens": 4096, "max_cost": 0.4})
    usage, errors = parallel.receipt_usage(tmp_path, config["teacher"])
    assert usage["unknown_cost"] == 0.4 and usage["requests"] == 2
    assert "provider_usage_unknown" in errors


@pytest.mark.parametrize("code", [401, 402, 403])
def test_global_auth_or_funding_stops(code):
    assert (
        parallel.stop_reason({"provider_http_status": code, "errors": []}, 0)[0]
        == "provider_auth_or_funding"
    )


def test_local_errors_do_not_stop_other_tasks():
    for error in (
        "provider_token_bound",
        "evaluation_runtime",
        "official_task_failure",
        "protocol_correction",
        "business_api_error",
        "snapshot_replay_unverified",
        "training_format_deferred",
        "tool_batch_interrupted",
    ):
        assert parallel.stop_reason(
            {"provider_http_status": None, "errors": [error]}, 0
        ) == (None, 0)
    bad = {"provider_http_status": None, "errors": ["worker_environment"]}
    assert parallel.stop_reason(bad, 0) == (None, 1)
    assert parallel.stop_reason(bad, 1)[0] == "consecutive_service_failures"


def test_review_refuses_mutated_source(tmp_path):
    source = tmp_path / "source"
    write_json(source / "result.json", {"synthetic": True})
    from recoveragent.demonstrations import sha256

    review = {
        "directory": str(source),
        "raw_result_sha256": sha256(source / "result.json"),
        "trajectory_sha256": None,
    }
    out = tmp_path / "review"
    write_json(out / "review.json", review)
    write_json(out / "manifest.json", {"files": files_manifest(out)})
    assert parallel.read_review(out) == review
    (source / "result.json").write_text("changed")
    with pytest.raises(ValueError, match="Reviewed source"):
        parallel.read_review(out)


def simulate(monkeypatch, tmp_path, *, fatal=False, workers=3):
    config, rows = fresh.campaign_config(parallel.DEFAULT)
    config["parallel_collection"]["workers"] = workers
    rows = rows[5:11]
    monkeypatch.setattr(fresh, "campaign_config", lambda _: (config, rows))
    monkeypatch.setattr(parallel, "inherited_records", lambda *a: {})
    monkeypatch.setattr(fresh, "environment_check", lambda: None)
    monkeypatch.setattr(
        fresh, "provider_preflight", lambda *a: {"status": "READY_FOR_PILOT"}
    )
    monkeypatch.setattr(parallel, "read_credentials", lambda _: {})
    monkeypatch.setattr(parallel, "authorize_task", lambda *a: None)
    state = {"active": 0, "peak": 0, "started": []}
    lock = threading.Lock()
    gate = threading.Barrier(workers)

    def fake(config, row, directory, identity, credentials, reviews):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            state["started"].append(row["task_id"])
            number = len(state["started"])
        if number <= workers:
            gate.wait(timeout=5)
        time.sleep(0.01 if number == 1 else 0.05)
        review = {
            "task_id": row["task_id"],
            "original_status": "FAILED",
            "errors": ["official_task_failure"],
            "provider_http_status": 402 if fatal and number == 1 else None,
            "usage": {"requests": 1, "outputs": 3, "cost": 0.001, "unknown_cost": 0},
            "directory": str(directory),
            "raw_result_sha256": None,
            "trajectory_sha256": None,
        }
        out = reviews / row["task_id"]
        write_json(out / "review.json", review)
        write_json(out / "manifest.json", {"files": files_manifest(out)})
        with lock:
            state["active"] -= 1
        return review

    monkeypatch.setattr(parallel, "run_one", fake)
    done = parallel.collect(Path("synthetic"), tmp_path, None)
    return state, done, rows


def test_three_workers_overlap_errors_continue_and_resume_does_not_pay(
    monkeypatch, tmp_path
):
    state, done, rows = simulate(monkeypatch, tmp_path)
    assert state["peak"] == 3 and len(done) == len(rows) == 6
    assert state["started"] == [r["task_id"] for r in rows]
    monkeypatch.setattr(parallel, "run_one", lambda *a: pytest.fail("Paid duplicate"))
    assert len(parallel.collect(Path("synthetic"), tmp_path, None)) == 6


def test_fatal_service_stops_new_dispatch_but_keeps_inflight(monkeypatch, tmp_path):
    state, done, _ = simulate(monkeypatch, tmp_path, fatal=True)
    assert len(state["started"]) == len(done) == 3
    assert (
        read_json(tmp_path / "global_stop.json")["reason"] == "provider_auth_or_funding"
    )
    assert parallel.collect(Path("synthetic"), tmp_path, None) is None


def test_audit_exception_retains_claim_and_unknown_reserve(monkeypatch, tmp_path):
    config, rows = fresh.campaign_config(parallel.DEFAULT)
    worker = fresh.worker_config(
        config, rows[10], {"cost": 0, "requests": 0, "outputs": 0}
    )
    monkeypatch.setattr(fresh, "run_child", lambda *a, **k: None)
    monkeypatch.setattr(
        fresh, "finish_attempt", lambda *a, **k: (_ for _ in ()).throw(ValueError())
    )
    monkeypatch.setattr(
        parallel, "audit_attempt", lambda *a, **k: (_ for _ in ()).throw(KeyError())
    )
    review = parallel.run_one(
        worker, rows[10], tmp_path / "fake", "synthetic", None, tmp_path / "reviews"
    )
    assert not review["eligible_for_sft"]
    assert review["usage"]["unknown_cost"] == worker["remaining_cost"]
    assert review["original_status"] == "AUDIT_ERROR"
    assert parallel.read_review(tmp_path / "reviews" / rows[10]["task_id"]) == review


def test_export_refuses_live_campaign_and_keeps_unknown_separate(monkeypatch, tmp_path):
    import fcntl

    exporter = fresh.script_helpers("export_fresh_parallel")
    config, plan = fresh.campaign_config(parallel.DEFAULT)
    rows = plan[:2]
    monkeypatch.setattr(exporter, "campaign_config", lambda _: (config, rows))
    root = tmp_path / "campaign"
    write_json(root / "campaign.json", config)
    destination = tmp_path / "bundle"
    with (root / "campaign.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            exporter.export(
                parallel.DEFAULT, root, destination, tmp_path / "public.json"
            )
    assert not destination.exists()
    review = {
        "task_id": rows[0]["task_id"],
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
            "unknown_cost": 0.4,
        },
        "completed_tool_results": 0,
    }
    home = root / "reviews" / rows[0]["task_id"]
    write_json(home / "review.json", review)
    write_json(home / "manifest.json", {"files": files_manifest(home)})
    public = exporter.export(
        parallel.DEFAULT, root, destination, tmp_path / "public.json"
    )
    assert public["summary"]["unknown_original_results"] == 1
    assert public["summary"]["official_failures"] == 0
    assert public["summary"]["not_run"] == 1
    assert public["summary"]["trainable_candidates"] == 0
    assert read_json(destination / "manifest.json")["complete_a244"] is False
    assert (destination / "trainable_candidates.jsonl").read_text() == ""


def test_transport_task_errors_continue_without_paid_retry_or_zero_cost():
    review = {
        "provider_http_status": None,
        "errors": ["provider_usage_unknown"],
        "usage": {"unknown_cost": 0.4},
    }
    assert parallel.stop_reason(review, 1, collect_transport_errors=True) == (None, 0)
    assert review["usage"]["unknown_cost"] == 0.4
    review["provider_http_status"] = 402
    assert (
        parallel.stop_reason(review, 1, collect_transport_errors=True)[0]
        == "provider_auth_or_funding"
    )


def test_serial_config_and_dispatch_preserve_scope_without_overlap(
    monkeypatch, tmp_path
):
    serial, rows = fresh.campaign_config(parallel.DEFAULT)
    original = read_json("configs/full732_a244_teacher_serial_20261028.json")
    assert serial["fresh_campaign"]["tasks"] == original["fresh_campaign"]["tasks"]
    assert len(rows) == 244
    assert serial["parallel_collection"]["workers"] == 1
    for key in ("limits", "student", "actor_prompt_variant", "tool_mode"):
        assert serial[key] == original[key]
    assert len(serial["parallel_collection"]["continuation_reviews"]["reviews"]) == 51
    state, done, rows = simulate(monkeypatch, tmp_path, workers=1)
    assert state["peak"] == 1 and len(done) == len(rows) == 6
    assert state["started"] == [r["task_id"] for r in rows]
    monkeypatch.setattr(parallel, "run_one", lambda *a: pytest.fail("Paid duplicate"))
    assert len(parallel.collect(Path("synthetic"), tmp_path, None)) == 6
