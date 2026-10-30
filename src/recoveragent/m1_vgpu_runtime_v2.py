"""Keep immutable status history while publishing atomic live progress pointers.

This independent entry point preserves the frozen probe and inference sources.
Only controller status files are mutable; task evidence remains create-once.
"""

import os
from pathlib import Path
import subprocess
import sys
import uuid

from . import full732_c244 as original
from . import m1_inference_probe, m1_vgpu_inference, m1_vgpu_resume
from .demonstrations import sha256
from .io import read_json, write_json

LOCK = original.ROOT / "configs/m1_vgpu_runtime_v2_20261030_lock.json"
LIVE_NAMES = frozenset(
    {"progress.json", "active.json", "artifact_index.json", "summary.json"}
)
MODULE = "recoveragent.m1_vgpu_runtime_v2"


def verify_lock():
    m1_vgpu_inference.verify_lock()
    m1_inference_probe.verify_source()
    for name, expected in read_json(LOCK)["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(original.ROOT / name) != expected
        ):
            raise ValueError("Runtime v2 source changed: " + name)
    return sha256(LOCK)


def publish_json(path, value):
    path = Path(path)
    if path.name not in LIVE_NAMES:
        return write_json(path, value)
    history = path.parent / "status_history" / path.stem
    snapshot = history / (uuid.uuid4().hex + ".json")
    write_json(snapshot, value)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        os.link(snapshot, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def child_command(command):
    command = list(command)
    if len(command) >= 3 and command[1] == "-m":
        if command[2] == "recoveragent.m1_inference_probe":
            command[2:3] = [MODULE, "probe"]
        elif command[2] == "recoveragent.m1_vgpu_inference":
            command[2] = MODULE
    return command


class ProcessCommands:
    def __getattr__(self, name):
        return getattr(subprocess, name)

    def Popen(self, command, **kwargs):
        return subprocess.Popen(child_command(command), **kwargs)


def modules():
    probe = original.isolated_module(m1_inference_probe, "_vgpu_v2_probe")
    probe.write_json = publish_json
    probe.verify_source = verify_lock
    probe.subprocess = ProcessCommands()
    inference = original.isolated_module(m1_vgpu_inference, "_vgpu_v2_inference")
    inference.probe = probe
    inference.verify_lock = verify_lock
    inference.VARIANT = "m1_vgpu32_session_deadline_status_v2_20261030"
    resume = original.isolated_module(m1_vgpu_resume, "_vgpu_v2_resume")
    resume.inference = inference
    resume.write_json = publish_json
    resume.subprocess = ProcessCommands()
    return probe, inference, resume


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"probe", "serve", "worker", "run"}:
        raise SystemExit("Expected probe, serve, worker, or run")
    verify_lock()
    probe, inference, resume = modules()
    command = sys.argv[1]
    if command in {"probe", "run"}:
        del sys.argv[1]
    {"probe": probe, "serve": inference, "worker": inference, "run": resume}[
        command
    ].main()


if __name__ == "__main__":
    main()
