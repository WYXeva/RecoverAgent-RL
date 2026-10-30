"""Offline contracts only: these tests do not establish GPU speed or M1 fidelity."""

import copy
import signal
import subprocess

import pytest

from recoveragent import m1_inference_probe as probe
from recoveragent.io import read_json


def measurements(seconds):
    return [
        [
            dict(generation_seconds=seconds, output_tokens=128, input_tokens=n)
            for _ in range(3)
        ]
        for n in (2048, 16384, 32633)
    ]


def numeric(error=0.01, matches=True):
    return [dict(max_abs_logit_error=error, first_greedy_token_matches=matches)] * 3


def test_measured_speedup_is_separate_from_production_and_bitwise_equivalence():
    result = probe.compare(
        measurements(4), measurements(2), numeric(), read_json(probe.PLAN)
    )
    assert result["status"] == "MICROBENCHMARK_PASS"
    assert result["per_prompt_median_speedup"] == [2, 2, 2]
    assert result["production_activation"] is False
    assert result["bitwise_or_sampled_sequence_equivalence_claim"] is False


@pytest.mark.parametrize(
    "error,matches", [(0.26, True), (float("nan"), True), (0.0, False)]
)
def test_numerical_mismatch_cannot_be_accepted_as_speedup(error, matches):
    result = probe.compare(
        measurements(4), measurements(2), numeric(error, matches), read_json(probe.PLAN)
    )
    assert result["status"] == "NUMERICAL_MISMATCH"


def test_slower_candidate_is_reported_without_retry_or_activation():
    result = probe.compare(
        measurements(2), measurements(4), numeric(), read_json(probe.PLAN)
    )
    assert result["status"] == "NO_MEASURED_SPEEDUP"


@pytest.mark.parametrize(
    "change", ["tokens", "prompt", "nan", "negative", "missing", "repeat"]
)
def test_incomparable_or_incomplete_workloads_rejected(change):
    rows = copy.deepcopy(measurements(2))
    if change == "tokens":
        rows[0][0]["output_tokens"] = 12
    elif change == "prompt":
        rows[0][0]["input_tokens"] = 7
    elif change == "nan":
        rows[0][0]["generation_seconds"] = float("nan")
    elif change == "negative":
        rows[0][0]["generation_seconds"] = -1
    elif change == "missing":
        rows.pop()
    else:
        rows[0].pop()
    with pytest.raises(ValueError):
        probe.compare(measurements(4), rows, numeric(), read_json(probe.PLAN))


def test_merge_requests_safe_merge_and_rejects_remaining_lora():
    class Model:
        def merge_and_unload(self, *, safe_merge):
            assert safe_merge is True
            return self

        def eval(self):
            return self

        def named_parameters(self):
            return [("lora_A.weight", object())]

    with pytest.raises(ValueError, match="remain"):
        probe.merge_in_memory(Model())


def test_default_command_does_not_load_weights_or_launch_worker(monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, "argv", ["probe"])
    monkeypatch.setattr(probe, "verify_source", lambda: "test")

    def forbidden(*args, **kwargs):
        pytest.fail("default started compute")

    monkeypatch.setattr(probe, "run_worker", forbidden)
    monkeypatch.setattr(probe, "supervise", forbidden)
    probe.main()
    assert "PLAN_ONLY" in capsys.readouterr().out


def test_timeout_kills_only_child_group_preserves_log_and_never_retries(
    tmp_path, monkeypatch
):
    launched, killed = [], []

    class Child:
        pid = 123456

        def wait(self, timeout=None):
            if timeout is not None:
                assert timeout == 300
                raise subprocess.TimeoutExpired("probe", timeout)
            return -9

    def launch(command, **kwargs):
        assert command[1:3] == ["-m", "recoveragent.m1_inference_probe"]
        assert kwargs["start_new_session"] is True
        launched.append(command)
        return Child()

    monkeypatch.setattr(probe.subprocess, "Popen", launch)
    monkeypatch.setattr(probe.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    output = tmp_path / "probe"
    with pytest.raises(subprocess.TimeoutExpired):
        probe.supervise(tmp_path / "adapter", output, read_json(probe.PLAN))
    assert len(launched) == 1 and killed == [(123456, signal.SIGKILL)]
    assert read_json(output / "supervisor.json")["automatic_retry"] is False
    assert (output / "worker.log").exists()


def test_checkpoint_output_overlap_and_existing_output_rejected(tmp_path):
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    for output in (adapter, adapter / "nested", tmp_path):
        with pytest.raises(ValueError):
            probe.supervise(adapter, output, read_json(probe.PLAN))
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        probe.supervise(adapter, existing, read_json(probe.PLAN))
