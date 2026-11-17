"""Import only the frozen nine accepted tasks and public train100 histories."""

import hashlib
import tarfile
from pathlib import Path

from .coevolve_tasks import validate_task
from .demonstrations import sha256
from .io import read_json
from .s2_state import evidence
from .v2_tasks import ROOT


def prepare(archive, output):
    spec = read_json(ROOT / "configs/v2_s2_inputs_20261116.json")
    output = Path(output).resolve()
    if sha256(archive) != spec["archive_sha256"]:
        raise ValueError("Initial task archive SHA mismatch")
    wanted = {
        row["archive_member"]: row["sha256"]
        for row in spec["initial_generated_tasks"] + spec["original_public_histories"]
    }
    seen = set()
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            if member.name not in wanted:
                continue
            if (
                not member.isfile()
                or member.name in seen
                or ".." in Path(member.name).parts
            ):
                raise ValueError("Invalid initial evidence archive member")
            raw = tar.extractfile(member).read()
            if hashlib.sha256(raw).hexdigest() != wanted[member.name]:
                raise ValueError("Initial evidence member changed")
            target = output / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if sha256(target) != wanted[member.name]:
                    raise ValueError("Existing imported evidence changed")
            else:
                with target.open("xb") as stream:
                    stream.write(raw)
            seen.add(member.name)
    if seen != set(wanted):
        raise ValueError("Incomplete initial evidence")
    pool = [
        read_json(output / row["archive_member"])
        for row in spec["initial_generated_tasks"]
    ]
    for task in pool:
        validate_task(task, task["source_task_id"])
    bundle = {
        "archive_sha256": spec["archive_sha256"],
        "pool": pool,
        "feedback": [
            {
                "task_id": row["task_id"],
                "generated_task_id": None,
                "directory": str((output / row["archive_member"]).parent),
            }
            for row in spec["original_public_histories"]
        ],
    }
    evidence(output / "bundle.json", bundle)
    evidence(
        output / "manifest.json",
        {
            "archive_sha256": spec["archive_sha256"],
            "files": {
                **wanted,
                "bundle.json": sha256(output / "bundle.json"),
            },
        },
    )
    return bundle


def load_bundle(directory):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    if manifest.get("revision") == "llm_initial_pool_v1":
        from .s2_llm_pool import load

        return load(directory)
    spec = read_json(ROOT / "configs/v2_s2_inputs_20261116.json")
    expected = {
        r["archive_member"]: r["sha256"]
        for r in spec["initial_generated_tasks"] + spec["original_public_histories"]
    }
    if (
        manifest["archive_sha256"] != spec["archive_sha256"]
        or {k: v for k, v in manifest["files"].items() if k != "bundle.json"}
        != expected
    ):
        raise ValueError("Initial bundle provenance changed")
    for name, value in manifest["files"].items():
        if sha256(directory / name) != value:
            raise ValueError("Initial evidence changed: " + name)
    # Rebuild paths for a relocated bundle without changing its historical messages.
    bundle = read_json(directory / "bundle.json")
    if bundle["pool"] != [
        read_json(directory / r["archive_member"])
        for r in spec["initial_generated_tasks"]
    ]:
        raise ValueError("Initial pool differs from its verified accepted records")
    bundle["feedback"] = [
        {
            "task_id": r["task_id"],
            "generated_task_id": None,
            "directory": str((directory / r["archive_member"]).resolve().parent),
        }
        for r in spec["original_public_histories"]
    ]
    for task in bundle["pool"]:
        validate_task(task, task["source_task_id"])
    if [t["task_id"] for t in bundle["pool"]] != [
        r["task_id"] for r in spec["initial_generated_tasks"]
    ]:
        raise ValueError("Initial generated pool changed")
    return bundle
