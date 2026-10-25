"""External immutable snapshots; database state and visible history travel together."""

from pathlib import Path

from .io import digest, files_manifest, read_json, validate_history, write_json
from .teacher_history import validate_history as validate_batch_history

POSITIONS = {"post_action", "pre_action", "pre_termination", "budget_exhausted"}


def capture(
    adapter, destination, messages, tools, position, metadata, *, agent_runtime=None
):
    if position not in POSITIONS:
        raise ValueError("Unknown checkpoint position")
    validator = (
        validate_batch_history if agent_runtime is not None else validate_history
    )
    validator(messages)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    state_id = "cp_" + destination.name
    adapter.export_state(state_id, destination / "dbs")
    context = {"messages": messages, "tools": tools}
    runtime = adapter.runtime()
    write_json(destination / "context.json", context)
    write_json(destination / "runtime.json", runtime)
    if agent_runtime is not None:
        write_json(destination / "agent_runtime.json", agent_runtime)
    manifest = {
        "format_version": 2 if agent_runtime is not None else 1,
        "task_id": adapter.task_id,
        "split": "train",
        "position": position,
        "history_end_index": len(messages),
        "history_hash": digest(messages),
        "tool_schema_hash": digest(tools),
        "logical_state_hash": adapter.fingerprint(),
        "versions": adapter.versions,
        "files": files_manifest(destination),
        "metadata": metadata,
    }
    manifest["checkpoint_id"] = digest(manifest)
    write_json(destination / "manifest.json", manifest)
    return str(destination.resolve())


def inspect_snapshot(source):
    source = Path(source)
    manifest = read_json(source / "manifest.json")
    identity = manifest.pop("checkpoint_id")
    if digest(manifest) != identity:
        raise ValueError("Checkpoint manifest was modified")
    manifest["checkpoint_id"] = identity
    actual = files_manifest(source)
    actual.pop("manifest.json")
    if actual != manifest["files"]:
        raise ValueError("Checkpoint files were modified")
    context = read_json(source / "context.json")
    version = manifest.get("format_version")
    if version not in {1, 2}:
        raise ValueError("Unsupported checkpoint format")
    validator = validate_batch_history if version == 2 else validate_history
    validator(context["messages"])
    if version == 2 and "agent_runtime.json" not in manifest["files"]:
        raise ValueError("Batch checkpoint requires agent runtime")
    if (
        digest(context["messages"]) != manifest["history_hash"]
        or digest(context["tools"]) != manifest["tool_schema_hash"]
        or len(context["messages"]) != manifest["history_end_index"]
    ):
        raise ValueError("Checkpoint history/schema mismatch")
    if manifest["split"] != "train":
        raise ValueError("Only train snapshots are supported")
    return manifest, context, read_json(source / "runtime.json")


def restore(adapter, source):
    manifest, context, runtime = inspect_snapshot(source)
    if (
        manifest["task_id"] != adapter.task_id
        or manifest["versions"] != adapter.versions
    ):
        raise ValueError("Task or base-data/code version mismatch")
    adapter.import_state(
        Path(source) / "dbs", "imported_" + manifest["checkpoint_id"][:16]
    )
    adapter.restore_runtime(runtime)
    if adapter.fingerprint() != manifest["logical_state_hash"]:
        raise ValueError("Imported logical state does not match source")
    if adapter.runtime() != runtime:
        raise ValueError("Imported runtime does not match source")
    inspect_snapshot(source)
    return manifest, context
