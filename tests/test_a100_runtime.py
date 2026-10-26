"""Hardware gate fixtures, never a substitute for real BF16 training smoke."""

import sys
from types import SimpleNamespace

import pytest

from recoveragent import sft_a100_runtime as runtime


@pytest.mark.parametrize(
    "name,gib,bf16,extra,ready",
    [
        ("NVIDIA A100-SXM4-80GB", 80, True, [], True),
        ("NVIDIA A100-SXM4-40GB", 40, True, [], False),
        ("NVIDIA A100-SXM4-80GB", 80, False, [], False),
        ("NVIDIA A100-SXM4-80GB", 80, True, ["torch_VERSION_MISMATCH"], False),
        ("NVIDIA RTX PRO 6000", 96, True, [], False),
    ],
)
def test_a100_preflight_keeps_all_nonhardware_gates(
    monkeypatch, name, gib, bf16, extra, ready
):
    monkeypatch.setattr(
        runtime,
        "original_preflight",
        lambda config: {"issues": ["REMOTE_96GB_GPU_NOT_VERIFIED", *extra]},
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True,
                device_count=lambda: 1,
                get_device_properties=lambda _: SimpleNamespace(
                    name=name, total_memory=gib * 1024**3
                ),
                is_bf16_supported=lambda: bf16,
            )
        ),
    )
    result = runtime.preflight({})
    assert (not result["issues"]) is ready
    assert all(e in result["issues"] for e in extra)
