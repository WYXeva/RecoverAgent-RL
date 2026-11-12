"""Private watchdog contracts with synthetic evidence and no external requests."""

import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "teacher_watchdog",
    Path(__file__).parents[1] / "scripts/watch_teacher_collection.py",
)
watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch)
REAL_PROBE = watch.probe_models


@pytest.fixture
def case(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    cfg = {
        "target_successes": 256,
        "episode_cost_cap_usd": 2,
        "total_cost_cap_usd": 100,
        "teacher": {
            "base_url": "https://example.invalid/v1",
            "model": "synthetic",
            "api_key_env": "KEY",
            "max_new_tokens": 4096,
            "pricing": {"currency": "USD"},
        },
    }
    monkeypatch.setattr(watch.collection, "ROOT", tmp_path)
    monkeypatch.setattr(watch.collection, "configuration", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(watch.collection, "verify_lock", lambda: "locked")
    monkeypatch.setattr(
        watch.collection, "worker_config", lambda *args: {"frozen": True}
    )
    monkeypatch.setattr(watch, "processes", lambda output: [])
    clock = [1000.0]
    monkeypatch.setattr(watch.time, "time", lambda: clock[0])
    watch.save(run / "config.json", cfg)
    watch.save(
        run / "campaign.json",
        {
            "source_lock_sha256": "locked",
            "reused_eligible": 154,
            "tasks": ["failed", "next"],
            "prior_attempts": [],
            "reuse": [],
        },
    )
    watch.save(
        run / "progress.json",
        {
            "status": "blocked_infrastructure",
            "updated_epoch": clock[0],
            "total_eligible": 154,
        },
    )
    directory = run / "attempts" / "run_failed"
    watch.save(directory.with_suffix(".config.json"), {"frozen": True})
    watch.save(directory / "trajectory.json", {"synthetic": True})
    fault = {
        "task_id": "failed",
        "status": "infrastructure_error",
        "eligible": False,
        "official_success": None,
        "termination": "infrastructure_error",
        "trajectory_sha256": watch.sha(directory / "trajectory.json"),
        "usage": {
            "unknown_cost_reservation": 0.3,
            "unknown_generation_reservation": 4096,
        },
        "provider_error": {
            "kind": "transport_error",
            "http_status": None,
            "usage_known": False,
            "diagnostic": {
                "transport_error_type": "ProxyError",
                "response_status": None,
            },
        },
    }
    watch.save(run / "claims/failed.json", {"task_id": "failed"})
    watch.save(run / "results/failed.json", fault)
    probes, launches = [], []

    def probe(*args):
        probes.append(args)
        return {"outcome": "ready", "http_status": 200, "teacher_present": True}

    def launch(command, **kwargs):
        launches.append((command, kwargs))
        return SimpleNamespace(pid=123456, poll=lambda: None)

    monkeypatch.setattr(watch, "probe_models", probe)
    monkeypatch.setattr(watch.subprocess, "Popen", launch)
    dog = watch.Watchdog(run, "locked")
    dog.state["absent_since"] = clock[0] - 31
    return SimpleNamespace(
        dog=dog,
        run=run,
        cfg=cfg,
        clock=clock,
        directory=directory,
        fault=fault,
        probes=probes,
        launches=launches,
    )


def test_healthy_heartbeat_does_not_probe_or_launch(case, monkeypatch):
    monkeypatch.setattr(
        watch,
        "processes",
        lambda _: [
            {"pid": 10, "kind": "collector", "start_ticks": 15, "elapsed_seconds": 40}
        ],
    )
    assert case.dog.tick()["status"] == "monitoring"
    assert not case.probes and not case.launches
    assert (
        watch.read(case.run / "watchdog/heartbeat.json")["collector_processes"][0][
            "pid"
        ]
        == 10
    )


def test_transient_fault_resumes_once_with_unchanged_result(case, monkeypatch):
    before = (case.run / "results/failed.json").read_bytes()
    assert case.dog.tick()["status"] == "resuming"
    assert len(case.launches) == len(case.probes) == 1
    command, kwargs = case.launches[0]
    assert command[-3:] == ["collect", "--output", str(case.run)]
    assert kwargs["start_new_session"] is True
    assert (case.run / "results/failed.json").read_bytes() == before
    audit = watch.read(next((case.run / "watchdog/resume_reviews").glob("*.json")))
    assert audit["faults"]["failed"]["usage"]["unknown_generation_reservation"] == 4096
    monkeypatch.setattr(
        watch,
        "processes",
        lambda _: [
            {
                "pid": 123456,
                "kind": "collector",
                "elapsed_seconds": 1,
                "start_ticks": 50,
            }
        ],
    )
    assert case.dog.tick()["status"] == "monitoring"
    assert len(case.launches) == 1


@pytest.mark.parametrize("status", [401, 402, 403, 429, 500])
def test_provider_http_fault_never_uses_models_as_quota_proof(case, status):
    case.fault["provider_error"]["http_status"] = status
    watch.save(case.run / "results/failed.json", case.fault)
    assert case.dog.tick()["status"] == "blocked"
    case.clock[0] += 90
    assert case.dog.tick()["status"] == "blocked"
    assert not case.probes and not case.launches


def test_network_wait_is_rate_limited(case, monkeypatch):
    seen = []
    monkeypatch.setattr(
        watch, "probe_models", lambda *args: seen.append(1) or {"outcome": "waiting"}
    )
    assert case.dog.tick()["status"] == "waiting_for_network"
    case.clock[0] += 30
    case.dog.tick()
    assert len(seen) == 1
    case.clock[0] += 31
    case.dog.tick()
    assert len(seen) == 2 and not case.launches


def test_get_models_permanent_error_stays_monitored_without_repeated_probe(
    case, monkeypatch
):
    seen = []
    monkeypatch.setattr(
        watch,
        "probe_models",
        lambda *args: seen.append(1)
        or {"outcome": "blocked", "reason": "models_http_error", "http_status": 429},
    )
    assert case.dog.tick()["status"] == "blocked"
    case.clock[0] += 90
    assert case.dog.tick()["status"] == "blocked"
    assert len(seen) == 1 and not case.launches


@pytest.mark.parametrize("stop_name", ["STOP", "watchdog/STOP"])
def test_stop_is_terminal_and_survives_file_removal(case, stop_name):
    stop = case.run / stop_name
    stop.touch()
    assert case.dog.tick()["status"] == "stopped_by_control_file"
    stop.unlink()
    assert case.dog.tick()["status"] == "stopped_by_control_file"
    assert not case.probes and not case.launches


@pytest.mark.parametrize("status", ["target_reached", "pool_exhausted", "cost_cap"])
def test_normal_completion_never_restarts(case, status):
    progress = watch.read(case.run / "progress.json")
    watch.save(case.run / "progress.json", {**progress, "status": status})
    assert case.dog.tick()["status"] == status
    assert not case.probes and not case.launches


def test_live_orphan_worker_is_waited_for_without_signals(case, monkeypatch):
    monkeypatch.setattr(
        watch,
        "processes",
        lambda _: [
            {"pid": 44, "kind": "worker", "start_ticks": 33, "elapsed_seconds": 1800}
        ],
    )
    assert case.dog.tick()["status"] == "waiting_for_orphan_workers"
    assert not case.probes and not case.launches


def test_missing_result_is_recovered_without_retry(case):
    (case.run / "results/failed.json").unlink()
    watch.save(case.directory / "result.json", case.fault)
    original = (case.directory / "result.json").read_bytes()
    assert case.dog.tick()["status"] == "resuming"
    assert (case.run / "results/failed.json").read_bytes() == original
    assert len(list((case.run / "watchdog/interrupt_reviews").glob("*.json"))) == 1


def test_interrupted_claim_seals_unknown_usage_and_continues_next_task(case):
    (case.run / "results/failed.json").unlink()
    watch.save(
        case.directory / "requests/001.reservation.json",
        {"max_tokens": 4096, "max_cost": 0.3},
    )
    assert case.dog.tick()["status"] == "resuming"
    result = watch.read(case.run / "results/failed.json")
    assert result["official_success"] is None and result["eligible"] is False
    assert result["usage"]["unknown_generation_reservation"] == 4096
    assert result["usage"]["unknown_cost_reservation"] == 0.3
    assert len(case.launches) == 1


def test_partial_json_blocks_interrupted_resolution_without_overwriting(case):
    (case.run / "results/failed.json").unlink()
    partial = case.directory / "partial.json"
    partial.write_text('{"incomplete":')
    with pytest.raises(json.JSONDecodeError):
        case.dog.tick()
    assert not (case.run / "results/failed.json").exists()
    assert partial.read_text() == '{"incomplete":'
    assert not case.probes and not case.launches


def test_disappearance_requires_two_observations(case):
    case.dog.state.pop("absent_since")
    assert case.dog.tick()["status"] == "confirming_collector_exit"
    assert not case.probes
    case.clock[0] += 31
    assert case.dog.tick()["status"] == "resuming"


def test_frozen_campaign_change_blocks_before_network(case):
    campaign = watch.read(case.run / "campaign.json")
    watch.save(case.run / "campaign.json", {**campaign, "tasks": ["changed"]})
    with pytest.raises(ValueError, match="Campaign input changed"):
        case.dog.tick()
    assert not case.probes and not case.launches


def test_watchdog_lock_prevents_second_monitor(case):
    with pytest.raises(BlockingIOError):
        watch.Watchdog(case.run, "locked")


def test_collector_lock_prevents_resume_race(case):
    with (case.run / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert case.dog.tick()["status"] == "collector_lock_busy"
    assert not case.launches


def test_manual_review_marks_only_exact_old_fault(case):
    watch.save(
        case.run / "resume_reviews/manual.json",
        {
            "action": "resume_same_campaign_on_next_unattempted_task",
            "source_lock_sha256": "locked",
            "exception_task": "failed",
            "trajectory_sha256": case.fault["trajectory_sha256"],
        },
    )
    case.dog.import_reviews()
    assert (
        watch.sha(case.run / "results/failed.json") in case.dog.state["handled_faults"]
    )
    case.fault["provider_error"]["http_status"] = 429
    watch.save(case.run / "results/failed.json", case.fault)
    assert case.dog.tick()["status"] == "blocked"
    assert not case.probes


def test_pid_without_matching_collector_command_is_not_alive(case):
    assert watch.process(os.getpid(), case.run) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("http_status", 429),
        ("usage_known", True),
        ("kind", "evaluation_error"),
    ],
)
def test_transport_classifier_is_narrow(case, field, value):
    case.fault["provider_error"][field] = value
    assert not watch.transient(case.fault)


@pytest.mark.parametrize(
    "http_status,outcome",
    [
        (200, "ready"),
        (401, "blocked"),
        (402, "blocked"),
        (429, "blocked"),
        (502, "waiting"),
        (503, "waiting"),
        (504, "waiting"),
    ],
)
def test_models_probe_uses_only_authenticated_get_and_handles_gateway_outage(
    case, monkeypatch, http_status, outcome
):
    import requests

    case.cfg["teacher"]["http_user_agent"] = "synthetic-agent"
    monkeypatch.setattr(
        watch,
        "read_credentials",
        lambda _: {
            "DEEPSEEK_BASE_URL": "https://example.invalid/v1",
            "DEEPSEEK_MODEL": "synthetic",
            "KEY": "synthetic-secret",
        },
    )

    class Response:
        status_code = http_status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def json(self):
            return {"data": [{"id": "synthetic"}]}

    def get(url, **kwargs):
        assert url == "https://example.invalid/v1/models"
        assert kwargs["headers"]["Authorization"] == "Bearer synthetic-secret"
        assert kwargs["headers"]["User-Agent"] == "synthetic-agent"
        assert kwargs["allow_redirects"] is False
        return Response()

    monkeypatch.setattr(requests, "get", get)
    assert REAL_PROBE(case.cfg, case.run / "unused.env")["outcome"] == outcome


def test_auth_failure_requires_changed_credentials_then_successful_probe(case):
    credentials = watch.collection.ROOT / ".env"
    credentials.write_text("synthetic-old")
    case.fault["provider_error"]["http_status"] = 401
    watch.save(case.run / "results/failed.json", case.fault)
    assert case.dog.tick()["status"] == "blocked"
    assert not case.probes
    credentials.write_text("synthetic-new")
    assert case.dog.tick()["status"] == "resuming"
    assert len(case.probes) == len(case.launches) == 1


def test_ssl_failure_waits_for_verified_https_before_resuming(case, monkeypatch):
    import requests

    case.fault["provider_error"]["diagnostic"]["transport_error_type"] = "SSLError"
    watch.save(case.run / "results/failed.json", case.fault)
    before = (case.run / "results/failed.json").read_bytes()
    monkeypatch.setattr(watch, "probe_models", REAL_PROBE)
    monkeypatch.setattr(
        watch,
        "read_credentials",
        lambda _: {
            "DEEPSEEK_BASE_URL": case.cfg["teacher"]["base_url"],
            "DEEPSEEK_MODEL": "synthetic",
            "KEY": "synthetic-secret",
        },
    )
    calls = []

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def json(self):
            return {"data": [{"id": "synthetic"}]}

    def get(url, **kwargs):
        assert url == case.cfg["teacher"]["base_url"] + "/models"
        assert kwargs.get("verify", True) is True
        assert kwargs["allow_redirects"] is False
        calls.append(1)
        if len(calls) == 1:
            raise requests.exceptions.SSLError("synthetic TLS failure")
        return Response()

    monkeypatch.setattr(requests, "get", get)
    assert case.dog.tick()["status"] == "waiting_for_network"
    assert not case.launches and "provider_hold" not in case.dog.state
    case.clock[0] += 30
    assert case.dog.tick()["status"] == "waiting_for_network"
    assert len(calls) == 1
    case.clock[0] += 31
    assert case.dog.tick()["status"] == "resuming"
    assert len(calls) == 2 and len(case.launches) == 1
    assert (case.run / "results/failed.json").read_bytes() == before
    audit = watch.read(next((case.run / "watchdog/resume_reviews").glob("*.json")))
    assert audit["faults"]["failed"]["usage"] == case.fault["usage"]
    assert audit["retry_failed_tasks"] is False


def test_invalid_probe_url_stays_blocked(case, monkeypatch):
    import requests

    monkeypatch.setattr(
        watch,
        "read_credentials",
        lambda _: {
            "DEEPSEEK_BASE_URL": case.cfg["teacher"]["base_url"],
            "DEEPSEEK_MODEL": "synthetic",
            "KEY": "synthetic-secret",
        },
    )

    def get(*args, **kwargs):
        raise requests.exceptions.InvalidURL("synthetic invalid URL")

    monkeypatch.setattr(requests, "get", get)
    assert REAL_PROBE(case.cfg, case.run / "unused.env")["outcome"] == "blocked"


def test_missing_final_pool_is_exported_without_any_generation(case):
    case.dog.cfg["target_successes"] = 154
    assert case.dog.tick()["status"] == "target_reached"
    manifest = next((case.run / "exports").glob("*/manifest.json"))
    assert (
        watch.read(manifest)["count"] == 0
    )  # This fixture contains no actual reused sample bodies.
    assert not case.launches and not case.probes
    assert case.dog.tick()["status"] == "target_reached"
    assert len(list((case.run / "exports").glob("*/manifest.json"))) == 1


def test_interrupted_attempt_with_recorded_quota_error_cannot_be_reclassified(case):
    (case.run / "results/failed.json").unlink()
    watch.save(
        case.directory / "trajectory.json",
        {
            "termination_reason": "infrastructure_error",
            "provider_error": {"http_status": 429},
            "usage": {},
        },
    )
    with pytest.raises(ValueError, match="provider error requires review"):
        case.dog.tick()
    assert not (case.run / "results/failed.json").exists()
    assert not case.launches and not case.probes


def test_partial_claim_blocks_even_when_result_exists(case):
    (case.run / "claims/failed.json").write_text('{"task_id":')
    with pytest.raises(json.JSONDecodeError):
        case.dog.tick()
    assert not case.probes and not case.launches


def test_recorded_execution_error_cannot_be_reclassified_as_process_interruption(case):
    (case.run / "results/failed.json").unlink()
    watch.save(
        case.directory / "trajectory.json",
        {
            "task_id": "failed",
            "status": "infrastructure_error",
            "termination_reason": "interrupted_batch",
            "error_type": "ValueError",
        },
    )
    with pytest.raises(ValueError, match="execution error requires review"):
        case.dog.tick()
    assert not (case.run / "results/failed.json").exists()
    assert not case.probes and not case.launches


@pytest.fixture
def evaluation_case(case, monkeypatch):
    from recoveragent.evaluation import classify_official

    context = {"task_id": "failed", "role": "train", "scope": "synthetic"}
    config = {"collection_run_id": "run", "custom_research": context}
    monkeypatch.setattr(
        watch.collection, "worker_config", lambda *args: copy.deepcopy(config)
    )
    watch.save(case.directory.with_suffix(".config.json"), config)
    watch.save(case.directory / "evaluation_research.json", context)
    observations = [
        {"exception_type": "AssertionError", "category": "task_assertion"},
        {"exception_type": "AttributeError", "category": "unknown_exception"},
        {"exception_type": "IndexError", "category": "task_state_failure"},
        {"exception_type": "UnboundLocalError", "category": "task_state_failure"},
    ]
    official = {
        "success": False,
        "num_tests": 5,
        "passes": [{"requirement": "synthetic passed test"}],
        "failures": [
            {
                "requirement": "synthetic requirement",
                "trace": "synthetic negative report",
            }
            for _ in observations
        ],
    }
    evaluated = {
        **classify_official(official, observations),
        "task_id": "failed",
        "experiment": case.directory.name,
        "environment": {"uri_roundtrip": True},
    }
    watch.save(case.directory / "isolated_evaluation.json", evaluated)
    trajectory = {
        "task_id": "failed",
        "trajectory_id": case.directory.name,
        "status": "infrastructure_error",
        "reward": None,
        "training_started": False,
        "termination_reason": "evaluation_error",
        "failure_category": "evaluation_runtime",
        "custom_research": context,
        "environment_check": {"uri_roundtrip": True},
        "evaluation_error": evaluated["exception_types"],
        "usage": case.fault["usage"],
    }
    watch.save(case.directory / "trajectory.json", trajectory)
    case.fault.update(
        termination="evaluation_error",
        provider_error=None,
        trajectory_sha256=watch.sha(case.directory / "trajectory.json"),
    )
    watch.save(case.directory / "result.json", case.fault)
    case.fault["worker_wall_seconds"] = 42.0
    watch.save(case.run / "results/failed.json", case.fault)
    return case


def test_complete_multi_exception_negative_report_continues_without_rescoring(
    evaluation_case,
):
    case = evaluation_case
    paths = list(case.directory.glob("*.json")) + [case.run / "results/failed.json"]
    before = {path: path.read_bytes() for path in paths}
    assert case.dog.tick()["status"] == "resuming"
    assert len(case.launches) == 1
    assert (case.run / "claims/failed.json").exists()
    assert not (
        case.run / "claims/next.json"
    ).exists()  # Only the original collector dispatches.
    assert all(path.read_bytes() == value for path, value in before.items())
    review = watch.read(next((case.run / "watchdog/quarantine_reviews").glob("*.json")))
    assert review["num_tests"] == 5 and review["failures"] == 4
    assert review["retained_official_success"] is None and review["eligible"] is False
    assert review["training_started"] is False and review["retry_failed_task"] is False
    assert review["actor_receives_evaluation"] is False
    assert "synthetic negative report" not in json.dumps(review)
    assert watch.read(case.run / "progress.json")["total_eligible"] == 154
    assert (
        watch.sha(case.run / "results/failed.json") in case.dog.state["handled_faults"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("official_result_complete", False),
        ("category", "environment_error"),
        ("task_id", "other"),
        ("experiment", "other"),
        ("success", False),
        ("official_success", True),
        ("environment", {"uri_roundtrip": False}),
        ("observations", []),
        ("exception_types", []),
    ],
)
def test_evaluation_quarantine_rejects_incomplete_environment_or_identity_error(
    evaluation_case, field, value
):
    case = evaluation_case
    path = case.directory / "isolated_evaluation.json"
    evaluated = watch.read(path)
    evaluated[field] = value
    watch.save(path, evaluated)
    assert case.dog.tick()["status"] == "blocked"
    assert not case.launches and not case.probes
    assert not (case.run / "watchdog/quarantine_reviews").exists()


@pytest.mark.parametrize(
    "change",
    [
        "num_tests",
        "observations",
        "environment_observation",
        "trace",
        "trajectory_hash",
        "attempt_result",
        "missing_report",
    ],
)
def test_evaluation_quarantine_rejects_broken_evidence(evaluation_case, change):
    case = evaluation_case
    path = case.directory / "isolated_evaluation.json"
    evaluated = watch.read(path)
    if change == "num_tests":
        evaluated["official"]["num_tests"] += 1
    elif change == "observations":
        evaluated["observations"].pop()
    elif change == "environment_observation":
        evaluated["observations"][1]["category"] = "environment_error"
    elif change == "trace":
        evaluated["official"]["failures"][0].pop("trace")
    elif change == "trajectory_hash":
        trajectory = watch.read(case.directory / "trajectory.json")
        watch.save(case.directory / "trajectory.json", {**trajectory, "changed": True})
    elif change == "attempt_result":
        watch.save(
            case.directory / "result.json", {**case.fault, "official_success": True}
        )
    watch.save(path, evaluated)
    if change == "missing_report":
        path.unlink()
    assert case.dog.tick()["status"] == "blocked"
    assert not case.launches and not case.probes


def test_evaluation_quarantine_checks_new_exception_names_without_allowlist(
    evaluation_case,
):
    case = evaluation_case
    path = case.directory / "isolated_evaluation.json"
    evaluated = watch.read(path)
    evaluated["observations"][1]["exception_type"] = "DifferentTaskStateException"
    evaluated["exception_types"][0] = "DifferentTaskStateException"
    watch.save(path, evaluated)
    trajectory = watch.read(case.directory / "trajectory.json")
    trajectory["evaluation_error"] = evaluated["exception_types"]
    watch.save(case.directory / "trajectory.json", trajectory)
    case.fault["trajectory_sha256"] = watch.sha(case.directory / "trajectory.json")
    watch.save(case.run / "results/failed.json", case.fault)
    watch.save(
        case.directory / "result.json",
        {k: v for k, v in case.fault.items() if k != "worker_wall_seconds"},
    )
    assert case.dog.tick()["status"] == "resuming"


def test_manual_evaluation_review_is_verified_and_persisted_before_live_monitoring(
    evaluation_case, monkeypatch
):
    case = evaluation_case
    watch.save(
        case.run / "resume_reviews/evaluation.json",
        {
            "action": "resume_same_campaign_on_next_unattempted_task",
            "source_lock_sha256": "locked",
            "exception_task": "failed",
            "result_sha256": watch.sha(case.directory / "result.json"),
            "trajectory_sha256": watch.sha(case.directory / "trajectory.json"),
            "evaluation_sha256": watch.sha(case.directory / "isolated_evaluation.json"),
        },
    )
    case.dog.import_reviews()
    digest = watch.sha(case.run / "results/failed.json")
    assert digest in case.dog.state["handled_faults"]
    assert (
        digest in watch.read(case.run / "watchdog/state.json")["evaluation_quarantines"]
    )
    monkeypatch.setattr(
        watch,
        "processes",
        lambda _: [{"pid": 12, "kind": "collector", "elapsed_seconds": 10}],
    )
    assert case.dog.tick()["status"] == "monitoring"
    assert not case.probes and not case.launches


def test_quarantine_evaluation_mutation_after_probe_blocks_dispatch(
    evaluation_case, monkeypatch
):
    case = evaluation_case

    def probe(*args):
        path = case.directory / "isolated_evaluation.json"
        value = watch.read(path)
        value["official"]["failures"][0]["trace"] = "changed during probe"
        watch.save(path, value)
        return {"outcome": "ready"}

    monkeypatch.setattr(watch, "probe_models", probe)
    assert case.dog.tick()["reason"] == "task_evaluation_quarantine_changed"
    assert not case.launches
