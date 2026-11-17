"""Bounded parallel sampling preserves the existing S2 experiment and resumes."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from recoveragent.io import read_json, write_json
from recoveragent.v2_runtime import (
    S2_GENERATION_EXECUTION,
    S2_BACKGROUND_EXECUTION,
    S2_ADMISSION_EXECUTION,
    S2_REVIEW_EXECUTION,
    S2_SUPERVISED_EXECUTION,
    S2_NATIVE_EXECUTION,
    S2_GOAL_EXECUTION,
    S2_PARALLEL_EXECUTION,
    S2_RUNTIME,
    verify_lock,
)
from test_s2_experiment import FakeRuntime, cfg, experiment


class ConcurrentRuntime(FakeRuntime):
    def __init__(self, config):
        super().__init__(config)
        self.guard = threading.Lock()
        self.active = self.peak = self.prepared = 0

    def prepare_rollouts(self, actor):
        assert self.active == 0
        self.prepared += 1

    def rollout(self, *args, **kwargs):
        assert self.prepared > 0
        with self.guard:
            self.active += 1
            self.peak = max(self.active, self.peak)
        try:
            time.sleep(0.02)
            return super().rollout(*args, **kwargs)
        finally:
            with self.guard:
                self.active -= 1

    def update(self, request, directory):
        assert self.active == 0
        return super().update(request, directory)


def test_parallel_order_seeds_policy_update_and_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_PARALLEL_EXECUTION)
    runtime = ConcurrentRuntime(cfg())
    run = experiment(tmp_path, runtime)
    run.group()
    run.group()
    assert runtime.peak == 2 and runtime.prepared == 2
    assert len({r[3] for r in runtime.calls}) == 8
    for index, path in enumerate(run.state["groups"]):
        rows = read_json(path)["episodes"]
        assert [r["seed"] for r in rows] == [
            cfg()["s2"]["seed"] + index * 4 + n for n in range(4)
        ]
        assert {r["policy"]["checkpoint_id"] for r in rows} == {"S0"}
    run.update()
    resumed = experiment(tmp_path, runtime)
    resumed.update()
    assert len(resumed.state["updates"]) == 1
    resumed.group()
    assert {r[0] for r in runtime.calls[8:]} == {"actor_1"}


def test_resume_serial_pending_without_replay_or_binding_change(tmp_path, monkeypatch):
    # The already-committed serial receipt and the interrupted claim survive intact.
    serial = FakeRuntime(cfg(), crash=2)
    original = experiment(tmp_path, serial)
    with pytest.raises(KeyboardInterrupt):
        original.group()
    binding = original.state["binding"]
    receipt = tmp_path / "groups/000/member_0.receipt.json"
    before = receipt.read_bytes()
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_PARALLEL_EXECUTION)
    runtime = ConcurrentRuntime(cfg())
    resumed = experiment(tmp_path, runtime)
    resumed.group()
    assert resumed.state["binding"] == binding and receipt.read_bytes() == before
    assert len(runtime.calls) == 2 and runtime.peak == 2
    rows = read_json(resumed.state["groups"][0])["episodes"]
    assert rows[1]["success"] is None


def test_new_execution_lock_explicit_old_lock_still_rejects(monkeypatch):
    monkeypatch.delenv("RECOVERAGENT_S2_EXECUTION", raising=False)
    with pytest.raises(ValueError, match="source changed|Incomplete"):
        verify_lock(S2_RUNTIME)
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_PARALLEL_EXECUTION)
    with pytest.raises(ValueError, match="source changed|Incomplete"):
        verify_lock(S2_RUNTIME)
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GENERATION_EXECUTION)
    with pytest.raises(ValueError, match="source changed|Incomplete"):
        verify_lock(S2_RUNTIME)
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_BACKGROUND_EXECUTION)
    with pytest.raises(ValueError, match="source changed|Incomplete"):
        verify_lock(S2_RUNTIME)
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_ADMISSION_EXECUTION)
    with pytest.raises(ValueError, match="source changed|Incomplete"):
        verify_lock(S2_RUNTIME)
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_REVIEW_EXECUTION)
    with pytest.raises(ValueError, match="source changed|Incomplete"):
        verify_lock(S2_RUNTIME)
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_SUPERVISED_EXECUTION)
    with pytest.raises(ValueError, match="source changed|Incomplete"):
        verify_lock(S2_RUNTIME)
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_NATIVE_EXECUTION)
    with pytest.raises(ValueError, match="source changed|Incomplete"):
        verify_lock(S2_RUNTIME)
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GOAL_EXECUTION)
    assert verify_lock(S2_RUNTIME)["execution_revision"] == S2_GOAL_EXECUTION
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", "unregistered")
    with pytest.raises(ValueError, match="Unknown S2 execution"):
        verify_lock(S2_RUNTIME)


def test_backend_sessions_are_per_thread(tmp_path):
    from recoveragent.v2_vllm import Backend

    proof = tmp_path / "proof.json"
    write_json(
        proof,
        {
            "policy": {"checkpoint_id": "actor"},
            "model_name": "actor",
            "max_model_len": 65536,
            "dtype": "bfloat16",
            "vllm_version": "0.19.0",
            "logprobs_mode": "processed_logprobs",
        },
    )
    backend = Backend(
        "http://127.0.0.1:18000", proof, {"checkpoint_id": "actor"}, "actor"
    )
    barrier = threading.Barrier(2)

    def session(_):
        first = backend.http
        barrier.wait(timeout=5)
        assert first is backend.http and not first.trust_env
        return first

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(session, range(2)))
    assert a is not b
