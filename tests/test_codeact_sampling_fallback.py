"""CPU mocks for capacity failures; no real OOM or model calls are injected."""

import copy
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from recoveragent.codeact import experiment, online, sampling
from recoveragent.io import read_json, write_json
from recoveragent.s2_schedule import GroupFeedback

ROOT = Path(__file__).resolve().parents[1]


def settings():
    return read_json(ROOT / "configs/codeact_grpo_coevolve_adaptive4_20261120.json")


def row(member, *, valid=True, fault=None, stage="sampling"):
    return {
        "member": member,
        "task_id": "synthetic",
        "generated_task_id": None,
        "initial_state_hash": "clean",
        "policy": {"checkpoint_id": "same"},
        "reward_valid": valid,
        "success": (member % 2 == 0) if valid else None,
        "fault": fault,
        "failure_stage": stage,
        "usage": {"requests": 3},
    }


def test_new_config_only_adds_task_local_fallback():
    old = read_json(ROOT / "configs/codeact_grpo_coevolve_parallel4_20261120.json")
    cfg = settings()
    expected = copy.deepcopy(cfg)
    expected["revision"] = old["revision"]
    expected["online"].pop("sampling_fallback")
    assert expected == old
    assert sampling.fallback_enabled(cfg)
    assert not sampling.fallback_enabled(old)
    cfg["online"]["sampling_fallback"]["max_retries"] = 2
    with pytest.raises(ValueError):
        sampling.fallback_enabled(cfg)


@pytest.mark.parametrize(
    "item,expected",
    [
        (row(1), False),  # normal business failure
        (row(1, valid=False, fault="CUDA out of memory"), True),
        (
            row(
                1, valid=False, fault="Student vLLM request failed: Connection refused"
            ),
            True,
        ),
        (
            row(1, valid=False, fault="Student vLLM request failed: 503 Server Error"),
            True,
        ),
        (row(1, valid=False, fault="Invalid CodeAct behavior logprobs"), False),
        (row(1, valid=False, fault="MemoryError", stage="judging"), False),
        (row(1, valid=False, fault="MemoryError", stage="evaluation"), False),
        (
            row(1, valid=False, fault="Teacher request timed out", stage="judging"),
            False,
        ),
        (
            row(1, valid=False, fault="Student vLLM request failed: 401 Client Error"),
            False,
        ),
    ],
)
def test_only_sampling_capacity_faults_retry(item, expected):
    assert sampling.retryable(item) is expected


def test_bounded_retry_preserves_results_costs_and_resets_next_task(tmp_path):
    calls = []
    original = [row(i) for i in range(4)]
    original[2] = row(2, valid=False, fault="CUDA out of memory")

    def execute(width, members, path):
        calls.append((width, members))
        return copy.deepcopy(original) if path.name == "primary" else [row(2)]

    def recover(*args):
        pytest.fail("No process crash in this test")

    args = ({"actor": "same", "seed": 9}, execute, recover, lambda: None)
    result = sampling.sample_with_fallback(tmp_path / "first", *args)
    assert calls == [(4, [0, 1, 2, 3]), (2, [2])]
    assert result["episodes"][1] == original[1]  # valid failure is never retried
    assert result["episodes"][0] == original[0]
    assert result["sampling_fallback"]["discarded_attempts"] == [original[2]]
    assert result["sampling_fallback"]["attempts"] == 5
    assert sampling.sample_with_fallback(tmp_path / "first", *args) == result
    assert len(calls) == 2  # same-protocol resume does not gain retries
    with pytest.raises(ValueError, match="binding"):
        sampling.sample_with_fallback(
            tmp_path / "first", {"actor": "changed"}, *args[1:]
        )
    sampling.sample_with_fallback(tmp_path / "second", *args)
    assert calls[2][0] == 4


def test_interrupted_two_way_phase_does_not_gain_clean_retry(tmp_path):
    calls = []

    def execute(width, members, path):
        calls.append(width)
        if width == 2:
            raise KeyboardInterrupt("controller interrupted")
        return [row(i, valid=False, fault="out of memory") for i in members]

    def recover(path, members, reason):
        return [
            dict(
                row(i, valid=False, fault=reason), failure_type="SamplingCapacityError"
            )
            for i in members
        ]

    args = ({"actor": "same"}, execute, recover, lambda: None)
    with pytest.raises(KeyboardInterrupt):
        sampling.sample_with_fallback(tmp_path, *args)
    result = sampling.sample_with_fallback(tmp_path, *args)
    assert calls == [4, 2]
    assert all(not r["reward_valid"] for r in result["episodes"])
    assert not experiment.effective(result["episodes"])


def test_startup_capacity_failure_then_two_way_failure_is_invalid(tmp_path):
    calls = []

    def execute(width, members, path):
        calls.append(width)
        raise sampling.SamplingCapacityError("backend killed by OOM")

    def recover(path, members, fault):
        return [
            dict(row(i, valid=False, fault=fault), failure_type="SamplingCapacityError")
            for i in members
        ]

    result = sampling.sample_with_fallback(tmp_path, {}, execute, recover, lambda: None)
    assert calls == [4, 2]
    assert all(e["success"] is None for e in result["episodes"])
    assert result["sampling_fallback"]["attempts"] == 8


def test_deadline_never_creates_extra_retry(tmp_path):
    checks = []

    def deadline():
        checks.append(True)
        if len(checks) > 1:
            raise TimeoutError("deadline")

    def execute(width, members, path):
        assert width == 4
        return [row(i, valid=False, fault="CUDA out of memory") for i in members]

    with pytest.raises(TimeoutError):
        sampling.sample_with_fallback(tmp_path, {}, execute, None, deadline)
    assert not (tmp_path / "retry_2").exists()


def test_real_controller_request_and_aggregate_path(tmp_path, monkeypatch):
    cfg = settings()
    checkpoint = {"adapter_sha256_after": "same_actor"}
    monkeypatch.setattr(experiment, "verify_checkpoint", lambda *args: checkpoint)
    widths, requests = [], []

    def server(width):
        widths.append(width)
        return SimpleNamespace(proof={"max_num_seqs": width}, receipt="server.json")

    def launch(dep, kind, config, req_path, target):
        req = read_json(req_path)
        requests.append(req)
        assert (
            kind == "sample"
            and req["server"]["max_num_seqs"] == req["sampling_concurrency"]
        )
        rows = []
        for i in req["members"]:
            r = row(
                i, valid=not (req["sampling_concurrency"] == 4 and i == 1), fault=None
            )
            if not r["reward_valid"]:
                r["fault"] = "CUDA out of memory"
            r.update(
                policy=online.policy(checkpoint, cfg),
                seed=cfg["seed"] + req["index"] * 4 + i,
                initial_state_hash="clean",
                directory=str(target / f"member_{i}"),
            )
            rows.append(r)
        write_json(target / "result.json", {"episodes": rows})
        return SimpleNamespace(wait=lambda **kw: 0, returncode=0)

    monkeypatch.setattr(experiment, "launch", launch)
    request = {
        "index": 0,
        "actor": "same",
        "selection": {"task_id": "synthetic", "source": "original"},
    }
    result = experiment.resilient_group(
        cfg,
        {"deadline": time.time() + 60},
        "config",
        request,
        tmp_path / "group",
        server,
    )
    assert widths == [4, 2]
    assert requests[1]["members"] == [1]
    assert all(e["reward_valid"] for e in result["episodes"])
    assert result["episodes"][1]["success"] is False
    assert result["effective"]
    # Aggregate includes wasted sampling cost but only four selected rewards.
    state = {
        "groups": [str(tmp_path / "group/result.json")],
        "status": "running",
        "optimizer_steps": 0,
        "pending": [],
        "pool": [],
        "generation_batches": [],
        "actor": "same",
        "started": 1,
    }
    report = experiment.summary(state, {"deadline": 99})
    assert report["completed_rollouts"] == 4 and report["sampling_retries"] == 1
    assert report["student_usage"]["requests"] == 15


def test_crash_recovery_keeps_complete_receipt_and_marks_partial_invalid(
    tmp_path, monkeypatch
):
    cfg = settings()
    task = cfg["online"]["train_tasks"][0]
    checkpoint = {"adapter_sha256_after": "same"}
    monkeypatch.setattr(experiment, "verify_checkpoint", lambda *args: checkpoint)
    request = {"index": 0, "actor": "same", "selection": {"task_id": task}}
    write_json(tmp_path / "member_0/live.json", {"usage": {"requests": 5}})
    result = experiment.sample_member(
        cfg, {}, request, tmp_path, 0, interrupted="worker killed"
    )
    assert sampling.retryable(result) and result["usage_unknown"]
    assert result["usage"]["requests"] == 5 and result["success"] is None
    assert not (tmp_path / "member_0/trajectory.json").exists()
    assert experiment.sample_member(cfg, {}, request, tmp_path, 0) == result
    rows = [dict(result, seed=result["seed"] + i) for i in range(4)]
    feedback = GroupFeedback(cfg["online"]["signals"])
    event = feedback.observe("interrupted", rows)
    assert not event["complete"] and event["signals"] == []


def killed_member(*args):
    os._exit(99)  # Intentional CPU-only child exit, not an actual OOM.


def test_actual_broken_process_pool_emits_recoverable_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "sample_member", killed_member)
    with pytest.raises(sampling.SamplingCapacityError):
        experiment.sample_group(
            settings(),
            {},
            {"server": {"max_num_seqs": 4}},
            tmp_path,
        )
    assert read_json(tmp_path / "phase_error.json")["type"] == "SamplingCapacityError"


def test_handoff_waits_existing_teacher_without_duplicate_calls(tmp_path, monkeypatch):
    identity = experiment.process_identity(os.getpid())
    assert identity
    worker = experiment.InheritedGeneration(
        {"pid": os.getpid(), "identity": identity}, tmp_path
    )
    assert worker.poll() is None
    with pytest.raises(experiment.subprocess.TimeoutExpired):
        worker.wait(timeout=0)
    write_json(tmp_path / "result.json", {"accepted": [], "attempts": []})
    assert worker.wait(timeout=0) == 0
    other = experiment.InheritedGeneration(
        {"pid": os.getpid(), "identity": "wrong-lifetime"}, tmp_path / "other"
    )
    assert other.poll() == 1
