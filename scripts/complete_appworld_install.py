"""Resolve pinned archive Git LFS pointers, verify hashes, then use official CLI.

Run after installing requirements/appworld.txt. Existing benchmark data is preserved.
"""

import argparse
import hashlib
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

REVISION = "42b5bcf3cd334fee33f0c37c02070a9f5807add5"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data/appworld")
    parser.add_argument("--cache", default=".cache/appworld")
    args = parser.parse_args()
    distribution = importlib.metadata.distribution("appworld")
    if REVISION not in (distribution.read_text("direct_url.json") or ""):
        raise ValueError("Install the pinned AppWorld revision first")
    source = Path(distribution.locate_file("appworld/.source"))
    for name in ["apps.bundle", "tests.bundle"]:
        path = source / name
        data = path.read_bytes()
        if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
            lines = data.decode().splitlines()
            expected = lines[1].split("sha256:")[1]
            expected_size = int(lines[2].split()[1])
            url = f"https://media.githubusercontent.com/media/StonyBrookNLP/appworld/{REVISION}/src/appworld/.source/{name}"
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = response.read()
            if (
                len(payload) != expected_size
                or hashlib.sha256(payload).hexdigest() != expected
            ):
                raise ValueError("Git LFS object checksum mismatch")
            path.write_bytes(payload)
    environment = dict(os.environ, APPWORLD_CACHE=str(Path(args.cache).resolve()))
    subprocess.run(
        [sys.executable, "-m", "appworld.cli", "install"],
        env=environment,
        check=True,
        timeout=120,
    )
    if not (Path(args.root) / "data").exists():
        subprocess.run(
            [
                sys.executable,
                "-m",
                "appworld.cli",
                "download",
                "data",
                "--root",
                args.root,
            ],
            env=environment,
            check=True,
            timeout=600,
        )
    else:
        print("Existing data preserved; validate its version before running")


if __name__ == "__main__":
    main()
