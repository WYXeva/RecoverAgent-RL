"""P3 native batches: ordered execution and complete observation boundaries only."""

from pathlib import Path

from .io import digest, files_manifest, write_json


def validate_history(messages):
    pending, seen = [], set()
    for message in messages:
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("Invalid message role")
        if pending:
            if role != "tool" or message.get("tool_call_id") != pending.pop(0):
                raise ValueError("Unmatched or out-of-order tool result")
        elif role == "tool":
            raise ValueError("Orphan tool result")
        calls = message.get("tool_calls") or []
        if calls:
            if role != "assistant":
                raise ValueError("Only Assistant can request tools")
            for call in calls:
                identity = call.get("id")
                if not isinstance(identity, str) or not identity or identity in seen:
                    raise ValueError("Duplicate or empty call id")
                seen.add(identity)
                pending.append(identity)
    if pending:
        raise ValueError("Cannot save an unfinished batch")


def capture_terminal(adapter, destination, messages, tools, metadata):
    """P3 terminal evidence; deliberately not a P0/P2 resumable checkpoint format."""
    validate_history(messages)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    adapter.export_state("p3a_terminal", destination / "dbs")
    write_json(destination / "context.json", {"messages": messages, "tools": tools})
    write_json(destination / "runtime.json", adapter.runtime())
    manifest = {
        "format_version": "p3a-terminal-batch-v1",
        "purpose": "terminal evidence before official evaluation; not P0/P2 replay",
        "task_id": adapter.task_id,
        "split": "train",
        "position": "post_action",
        "history_end_index": len(messages),
        "history_hash": digest(messages),
        "tool_schema_hash": digest(tools),
        "logical_state_hash": adapter.fingerprint(),
        "versions": adapter.versions,
        "metadata": metadata,
        "files": files_manifest(destination),
    }
    manifest["checkpoint_id"] = digest(manifest)
    write_json(destination / "manifest.json", manifest)
    return manifest["checkpoint_id"]
