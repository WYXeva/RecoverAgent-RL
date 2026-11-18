"""Freeze only code/configuration, then detach the existing serial collector."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def manifest(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--python", required=True, help="Existing pinned AppWorld Python 3.12"
    )
    p.add_argument("--root", required=True)
    p.add_argument("--credentials", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--config", default="configs/codeact_reboot_20261119.json")
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    pid_path = output / "collector.pid"
    if pid_path.exists():
        pid = int(pid_path.read_text())
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
        except FileNotFoundError:
            command = b""
        if b"recoveragent.codeact.cli" in command and str(output).encode() in command:
            print(
                json.dumps({"already_running": True, "pid": pid, "output": str(output)})
            )
            return
    snapshot = output / "runtime"
    receipt = output / "runtime_manifest.json"
    if receipt.exists():
        if manifest(snapshot) != json.loads(receipt.read_text()):
            raise ValueError("Frozen runtime changed")
    else:
        snapshot.mkdir(exist_ok=False)
        for name in ("src", "configs"):
            shutil.copytree(
                root / name,
                snapshot / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        receipt.write_text(json.dumps(manifest(snapshot), indent=2) + "\n")
    config = output / "config.json"
    if not config.exists():
        shutil.copyfile(args.config, config)
    elif json.loads(config.read_text()) != json.loads(Path(args.config).read_text()):
        raise ValueError("Use the original run configuration to resume")
    env = dict(
        os.environ, PYTHONPATH=str(snapshot / "src"), PYTHONDONTWRITEBYTECODE="1"
    )
    command = [
        str(Path(args.python).absolute()),
        "-m",
        "recoveragent.codeact.cli",
        "collect",
        "--config",
        str(config),
        "--output",
        str(output),
        "--root",
        str(Path(args.root).resolve()),
        "--credentials",
        str(Path(args.credentials).resolve()),
    ]
    with (output / "collector.log").open("a") as log:
        process = subprocess.Popen(
            command,
            cwd=snapshot,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (output / "collector.pid").write_text(str(process.pid) + "\n")
    print(
        json.dumps(
            {
                "pid": process.pid,
                "output": str(output),
                "runtime": str(snapshot),
                "started_not_completed": True,
            }
        )
    )


if __name__ == "__main__":
    main()
