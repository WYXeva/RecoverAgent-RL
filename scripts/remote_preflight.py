"""Read-only preflight to run separately on the future remote host; never installs/trains."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--scope", choices=["local-preparation", "remote"], required=True
    )
    args = parser.parse_args()
    versions = {}
    for name in ("torch", "transformers", "trl", "peft", "vllm", "appworld"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "NOT_INSTALLED"
    gpu = (
        subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if shutil.which("nvidia-smi")
        else None
    )
    issues = []
    output = gpu.stdout.strip() if gpu and gpu.returncode == 0 else ""
    if not output or len(output.splitlines()) != 1:
        issues.append("SINGLE_GPU_NOT_VERIFIED")
    elif "RTX PRO 6000" not in output or int(output.split(",")[1].strip()) < 90000:
        issues.append("TARGET_96GB_GPU_NOT_VERIFIED")
    for name in ("torch", "transformers", "trl", "peft"):
        if versions[name] == "NOT_INSTALLED":
            issues.append(name.upper() + "_MISSING")
    # Importing torch is read-only; no model weights or optimizer are created.
    cuda = {"available": False}
    if versions["torch"] != "NOT_INSTALLED":
        import torch

        cuda = {"available": torch.cuda.is_available(), "runtime": torch.version.cuda}
    if not cuda["available"]:
        issues.append("CUDA_NOT_VERIFIED")
    if args.scope != "remote":
        issues.append("THIS_IS_NOT_THE_REMOTE_HOST")
    report = {
        "scope": args.scope,
        "status": "PARTIAL" if not issues else "BLOCKED",
        "python": platform.python_version(),
        "packages": versions,
        "gpu": output or "UNAVAILABLE",
        "cuda": cuda,
        "disk_free_bytes": shutil.disk_usage(".").free,
        "issues": issues,
        "remaining_checks": [
            "authorized SSH and remote paths",
            "same-version AppWorld worker execution",
            "dataset SHA-256 and train membership",
            "cross-host snapshot/history import",
            "pinned 4B tokenizer mask",
            "single-GPU BF16 LoRA update/save/reload (future authorization)",
        ],
        "training_started": False,
        "remote_training_pass": False,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(args.output)


if __name__ == "__main__":
    main()
