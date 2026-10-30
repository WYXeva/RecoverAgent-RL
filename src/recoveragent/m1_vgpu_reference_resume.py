"""Continue the unattempted suffix with the original, unmerged M1 weights."""

import subprocess

from . import full732_c244 as original
from . import m1_vgpu_reference_backend as inference
from . import m1_vgpu_resume
from .m1_vgpu_runtime_v2 import publish_json


class ProcessCommands:
    def __getattr__(self, name):
        return getattr(subprocess, name)

    def Popen(self, command, **kwargs):
        command = list(command)
        if command[1:3] == ["-m", "recoveragent.m1_vgpu_inference"]:
            command[2] = "recoveragent.m1_vgpu_reference_backend"
        return subprocess.Popen(command, **kwargs)


def main():
    inference.verify_lock()
    runner = original.isolated_module(m1_vgpu_resume, "_reference_resume")
    runner.inference = inference
    runner.write_json = publish_json
    runner.subprocess = ProcessCommands()
    runner.main()


if __name__ == "__main__":
    main()
