"""Explicit A100 supplement; reuse the frozen Full732 runtime without file edits."""

import importlib.util
from pathlib import Path

from . import full732_sft_runtime as original
from .demonstrations import sha256
from .full732_sft_data import ROOT
from .io import read_json
from .sft_a100_runtime import preflight as existing_a100_preflight

HARDWARE = "full732_m1_a100_20261030"


def verify_lock():
    base_sha = original.verify_lock()
    path = ROOT / "configs/full732_m1_a100_20261030_lock.json"
    lock = read_json(path)
    if (
        lock["hardware_contract"] != HARDWARE
        or lock["base_runtime_lock_sha256"] != base_sha
    ):
        raise ValueError("A100 supplement or frozen base runtime differs")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("A100 supplement source differs: " + name)
    return sha256(path)


def hardware_preflight(config):
    # The existing A100 gate replaces only the 96GB-specific issue after
    # verifying one A100, >=75 GiB and BF16. All package checks are retained.
    return {**existing_a100_preflight(config), "hardware_contract": HARDWARE}


def load_runtime():
    # A separate module namespace prevents changing any caller of the original
    # PRO6000 runtime, even in the same Python process. Its execute body is exact.
    spec = importlib.util.spec_from_file_location(
        "recoveragent._full732_a100_implementation", original.__file__
    )
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    runtime.old_preflight = hardware_preflight
    runtime.verify_lock = verify_lock
    return runtime


if __name__ == "__main__":
    load_runtime().main()
