"""Record relevant package/resource metadata without reading credential values."""

import argparse
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    versions = {}
    for name in [
        "appworld",
        "pydantic",
        "torch",
        "transformers",
        "trl",
        "peft",
        "jsonschema",
    ]:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "NOT_INSTALLED"
    gpu = (
        subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if shutil.which("nvidia-smi")
        else None
    )
    result = {
        "python": platform.python_version(),
        "executable": sys.executable,
        "system": platform.platform(),
        "cpu_count": os.cpu_count(),
        "packages": versions,
        "gpu": gpu.stdout.strip()
        if gpu and gpu.returncode == 0
        else "UNAVAILABLE_IN_CURRENT_PROCESS",
        "credential_presence": {
            name: bool(os.environ.get(name))
            for name in ["STUDENT_API_KEY", "OPENAI_API_KEY", "HF_TOKEN"]
        },
        "disk_free_bytes": shutil.disk_usage(".").free,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(args.output)


if __name__ == "__main__":
    main()
