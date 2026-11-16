"""Bounded retention inside a new S2 run; preserve round actors and exact receipts."""

import tarfile
import os
from pathlib import Path

from .demonstrations import sha256
from .io import read_json
from .s2_state import atomic


def archive_requests(directory):
    directory = Path(directory)
    source = directory / "session/requests"
    if not source.exists():
        return
    manifest = directory / "session/requests_archive.json"
    archive = directory / "session/requests.tar.gz"
    if manifest.exists():
        proof = read_json(manifest)
        if sha256(archive) != proof["archive_sha256"]:
            raise ValueError("Archived sampling evidence changed")
    else:
        paths = sorted(p for p in source.iterdir() if p.is_file())
        if not paths:
            return
        expected = {p.name: sha256(p) for p in paths}
        temporary = archive.with_suffix(".tmp")
        with tarfile.open(temporary, "w:gz", compresslevel=1) as tar:
            for path in paths:
                tar.add(path, arcname=path.name)
        seen = {}
        import hashlib

        with tarfile.open(temporary, "r|gz") as tar:
            for member in tar:
                if member.isfile():
                    seen[member.name] = hashlib.sha256(
                        tar.extractfile(member).read()
                    ).hexdigest()
        if seen != expected:
            raise ValueError("Sampling archive member verification failed")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(archive)
        proof = {
            "archive_sha256": sha256(archive),
            "files": expected,
            "exact_raw_records_preserved": True,
        }
        atomic(manifest, proof)
    for name, value in proof["files"].items():
        path = source / name
        if path.exists():
            if sha256(path) != value:
                raise ValueError("Sampling receipt changed before compression cleanup")
            path.unlink()


def request_record(directory, name):
    directory = Path(directory)
    raw = directory / "session/requests" / name
    if raw.exists():
        return read_json(raw)
    manifest = directory / "session/requests_archive.json"
    if not manifest.exists():
        return None
    import hashlib
    import json

    proof = read_json(manifest)
    if name not in proof["files"]:
        return None
    with tarfile.open(directory / "session/requests.tar.gz", "r:gz") as tar:
        content = tar.extractfile(name).read()
    if hashlib.sha256(content).hexdigest() != proof["files"][name]:
        raise ValueError("Archived request changed")
    return json.loads(content)


def paired_inputs(baseline, results):
    previous = {r["task_id"]: r for r in baseline}
    checks = []
    for row in results:
        old = previous[row["task_id"]]
        a = request_record(old["directory"], "001.request.json")
        b = request_record(row["directory"], "001.request.json")
        if a is None or b is None:
            if old["success"] is not None and row["success"] is not None:
                raise ValueError("Scored evaluation lacks its first request")
            checks.append(
                {
                    "task_id": row["task_id"],
                    "same_input": None,
                    "same_initial_state": None,
                }
            )
            continue
        keys = (
            "messages",
            "tools",
            "input_token_ids",
            "max_input_tokens",
            "max_new_tokens",
            "temperature",
            "top_p",
            "seed",
        )
        same = all(a[k] == b[k] for k in keys)
        state = (
            old["initial_state_hash"] == row["initial_state_hash"]
            if old["initial_state_hash"] and row["initial_state_hash"]
            else None
        )
        if not same or state is False:
            raise ValueError(
                "Stage evaluation public input/budget/initial state differs from S0"
            )
        checks.append(
            {"task_id": row["task_id"], "same_input": same, "same_initial_state": state}
        )
    return checks


def retain_committed(state, output):
    """Only retire this run's superseded intermediate weights after durable commit."""
    root = (Path(output) / "updates").resolve()
    keep_actors = {state["actor"]} | {
        v["actor"] for v in state["round_evaluations"].values()
    }
    keep_optimizers = {state["optimizer"]} | {
        v["optimizer"] for v in state["round_evaluations"].values()
    }
    for name in state["updates"]:
        row = read_json(name)
        if not row["updated"] or row["actor"] in keep_actors:
            continue
        actor = Path(row["actor"]).resolve()
        if not actor.is_relative_to(root):
            raise ValueError("Refusing retention outside the new S2 update directory")
        receipt = read_json(actor / "checkpoint_receipt.json")
        intent = actor.parent / "retention.json"
        atomic(
            intent,
            {
                "policy": "keep_round_actors_and_latest_restart",
                "loadable": False,
                "adapter_sha256": row["actor_sha256"],
                "original_files": receipt["files"],
            },
        )
        for relative in receipt["files"]:
            path = actor / relative
            if (
                relative
                not in {
                    "reload_probe.json",
                    "reload_result.json",
                    "adapter_config.json",
                }
                and path.exists()
            ):
                path.unlink()
        optimizer = row.get("optimizer")
        if optimizer and optimizer not in keep_optimizers:
            path = Path(optimizer).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Refusing optimizer retention outside the new run")
            path.unlink(missing_ok=True)
