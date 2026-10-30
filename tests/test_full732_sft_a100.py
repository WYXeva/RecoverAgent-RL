"""Hardware variant only; no model or GPU execution."""

import inspect

from recoveragent import full732_sft_a100_runtime as a100
from recoveragent import full732_sft_runtime as original


def test_a100_namespace_reuses_exact_training_body_without_mutating_base():
    original_preflight = original.old_preflight
    runtime = a100.load_runtime()
    assert inspect.getsource(runtime.execute) == inspect.getsource(original.execute)
    assert runtime.old_preflight is a100.hardware_preflight
    assert runtime.verify_lock is a100.verify_lock
    assert original.old_preflight is original_preflight
    assert original.verify_lock is not a100.verify_lock


def test_hardware_variant_keeps_preflight_issues(monkeypatch):
    monkeypatch.setattr(
        a100,
        "existing_a100_preflight",
        lambda c: {"issues": ["torch_VERSION_MISMATCH"], "status": "BLOCKED"},
    )
    check = a100.hardware_preflight({})
    assert check["issues"] == ["torch_VERSION_MISMATCH"]
    assert check["status"] == "BLOCKED"
    assert check["hardware_contract"] == a100.HARDWARE
