"""External immutable snapshots; database state and visible history travel together."""

from pathlib import Path

from .io import digest, files_manifest, read_json, validate_history, write_json
from .teacher_history import validate_history as validate_batch_history

POSITIONS = {"post_action", "pre_action", "pre_termination", "budget_exhausted"}


def capture(
    adapter,
    destination,
    messages,
    tools,
    position,
    metadata,
    *,
    agent_runtime=None,
    task_memory=None,
):
    if position not in POSITIONS:
        raise ValueError("Unknown checkpoint position")
    validator = (
        validate_batch_history if agent_runtime is not None else validate_history
    )
    validator(messages)
    if task_memory is None and agent_runtime is not None and "memory" in agent_runtime:
        raise ValueError("Cannot omit Memory from a memory-enabled Agent checkpoint")
    if task_memory is not None:
        if agent_runtime is None or agent_runtime.get("memory") != task_memory.identity:
            raise ValueError("Memory snapshot requires matching Agent runtime")
        if agent_runtime.get("format") != task_memory.runtime:
            raise ValueError("Memory snapshot requires the EGTM runtime version")
        if task_memory.task_id != adapter.task_id:
            raise ValueError("Memory snapshot task mismatch")
        task_memory.validate_boundary(messages, tools)
        if task_memory.model_driven:
            _validate_model_runtime(task_memory, agent_runtime)
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
    if task_memory is not None:
        task_memory.save(destination / "memory", messages, tools)
    manifest = {
        "format_version": (4 if task_memory.model_driven else 3)
        if task_memory is not None
        else 2
        if agent_runtime is not None
        else 1,
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
    if version not in {1, 2, 3, 4}:
        raise ValueError("Unsupported checkpoint format")
    validator = validate_batch_history if version in {2, 3, 4} else validate_history
    validator(context["messages"])
    if version in {2, 3, 4} and "agent_runtime.json" not in manifest["files"]:
        raise ValueError("Batch checkpoint requires agent runtime")
    if version in {3, 4}:
        from .task_memory import TaskMemory

        memory = TaskMemory.load(
            source / "memory", context["messages"], context["tools"]
        )
        agent = read_json(source / "agent_runtime.json")
        if (
            memory.task_id != manifest["task_id"]
            or agent.get("memory") != memory.identity
            or agent.get("format") != memory.runtime
            or (version == 4) != memory.model_driven
        ):
            raise ValueError("Checkpoint Memory/runtime binding mismatch")
        if version == 4:
            _validate_model_runtime(memory, agent)
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


def _validate_model_runtime(memory, agent):
    if agent.get("usage", {}).get("unknown_generation_reservation", 0):
        raise ValueError("Unresolved generation cannot become a Memory checkpoint")
    usage = agent.get("usage", {})
    for key, expected in {
        "memory_update_requests": len(memory.updates),
        "memory_update_input_tokens": sum(
            len(r["request"]["input_token_ids"]) for r in memory.updates
        ),
        "memory_update_output_tokens": sum(
            len(r["response"]["token_ids"]) for r in memory.updates
        ),
        "memory_validation_failures": sum(
            r["status"] == "rejected" for r in memory.updates
        ),
    }.items():
        if usage.get(key) != expected:
            raise ValueError("Memory usage/receipt boundary mismatch")
    for receipt in memory.updates:
        if receipt["request"]["policy"] != agent.get("policy"):
            raise ValueError("Memory update came from a different Student policy")
        if int(receipt["request_id"]) > agent.get("usage", {}).get("model_requests", 0):
            raise ValueError("Memory receipt exceeds Agent request boundary")
