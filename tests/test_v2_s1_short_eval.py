"""Frozen trained weights and same vLLM protocol; no real model calls."""

import copy

import pytest

from recoveragent import v2_s1_short_eval as entry
from recoveragent.demonstrations import sha256
from recoveragent.io import read_json, write_json
from recoveragent.v2_runtime import verify_lock, S1_SHORT_RUNTIME


def test_backend_switch_preserves_training_lock_and_both_inference_budgets():
    cfg = entry.configuration()
    before = read_json(f"configs/{S1_SHORT_RUNTIME}.json")
    assert cfg["student_limits"] == before["student_limits"]
    assert cfg["answer_format_contract"] == before["answer_format_contract"]
    assert cfg["candidate"]["optimizer_steps"] == 40
    assert cfg["training_source_lock_sha256"] == sha256(before["source_lock"])
    assert cfg["inference"]["evaluation_backend"] == "vllm"
    assert cfg["inference"]["evaluation_concurrency"] == 1
    with pytest.raises(ValueError, match="source|Incomplete"):
        verify_lock(S1_SHORT_RUNTIME)


@pytest.mark.parametrize("damage", [None, "candidate", "steps", "parent", "file"])
def test_candidate_binding_rejects_wrong_weights_before_server_use(
    tmp_path, monkeypatch, damage
):
    cfg = copy.deepcopy(entry.configuration())
    saved = {
        "adapter_sha256_after": "candidate",
        "optimizer_steps": 40,
        "parent_receipt_sha256": cfg["s0"]["checkpoint_receipt_sha256"],
    }
    path = tmp_path / "s1/checkpoint_receipt.json"
    write_json(path, saved)
    cfg["candidate"] = {
        "adapter_sha256": "candidate",
        "checkpoint_receipt_sha256": sha256(path),
    }

    def checkpoint(path, cfg, initial=False):
        if initial:
            assert str(path) == "S0"
            return {}
        return saved

    monkeypatch.setattr(entry, "checkpoint", checkpoint)
    if damage == "candidate":
        saved["adapter_sha256_after"] = "raw_base"
    elif damage == "steps":
        saved["optimizer_steps"] = 20
    elif damage == "parent":
        saved["parent_receipt_sha256"] = "old_S1"
    elif damage == "file":
        path.write_text("changed")
    deployment = {"s0": "S0", "s1": str(path.parent)}
    if damage:
        with pytest.raises(ValueError, match="candidate"):
            entry.validate_models(deployment, cfg)
    else:
        entry.validate_models(deployment, cfg)
