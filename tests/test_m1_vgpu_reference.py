import copy

import pytest

from recoveragent import m1_vgpu_reference_backend as backend
from recoveragent.io import read_json


def receipt(monkeypatch):
    monkeypatch.setattr(backend.status_runtime, "verify_lock", lambda: "v2lock")
    plan = read_json(backend.probe.PLAN)
    return {
        "plan": plan,
        "policy": {"checkpoint_id": "adapter:" + plan["adapter_parameter_hash"]},
        "source_lock_sha256": "v2lock",
        "synthetic_only": True,
        "training": False,
        "C244_started": False,
        "measurements": {
            "reference": [
                [
                    {
                        "input_tokens": length,
                        "output_tokens": 128,
                        "token_ids": [1] * 128,
                        "generation_seconds": 1.0,
                        "peak_allocated_bytes": 12 * 1024**3,
                        "peak_reserved_bytes": 14 * 1024**3,
                    }
                    for _ in range(3)
                ]
                for length in [2073, 16409, 32633]
            ]
        },
        "numerical": [{"max_abs_logit_error": 0.5}],
    }


def test_reference_gate_is_separate_from_rejected_merge(monkeypatch):
    data = receipt(monkeypatch)
    backend.validate_probe(data, data["plan"]["adapter_parameter_hash"])
    assert "merge_in_memory" not in backend.serve.__code__.co_names


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", 32000),
        ("output_tokens", 127),
        ("generation_seconds", float("nan")),
        ("peak_reserved_bytes", 40 * 1024**3),
    ],
)
def test_invalid_reference_evidence_rejected(monkeypatch, field, value):
    data = copy.deepcopy(receipt(monkeypatch))
    data["measurements"]["reference"][-1][0][field] = value
    with pytest.raises(ValueError):
        backend.validate_probe(data, data["plan"]["adapter_parameter_hash"])
