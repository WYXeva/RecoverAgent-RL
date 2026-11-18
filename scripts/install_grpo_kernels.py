"""Install only the new convolution extension into an isolated Python overlay."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    target = args.target.resolve()
    target.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "--target",
            str(target),
            "-r",
            str(root / "requirements/grpo-kernels-20261118.txt"),
        ],
        check=True,
    )
    # A fresh process is necessary: Transformers resolves its backend on import.
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(target), str(root / "src"), os.environ.get("PYTHONPATH", "")]
        ),
    }
    subprocess.run(
        [
            sys.executable,
            str(root / "scripts/probe_grpo_kernels.py"),
            "--cuda-smoke",
            "--output",
            str(target / "kernel_smoke.json"),
        ],
        env=env,
        check=True,
    )
