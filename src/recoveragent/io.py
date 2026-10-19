"""Stable identities and append-only local evidence."""

import hashlib
import json
import os
from pathlib import Path


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    """Create evidence once; never silently overwrite an earlier run."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


def files_manifest(directory, exclude_python_cache=False):
    directory = Path(directory)
    result = {}
    for path in sorted(directory.rglob("*")):
        if exclude_python_cache and (
            "__pycache__" in path.parts or path.suffix == ".pyc"
        ):
            continue
        if path.is_symlink():
            raise ValueError("Evidence trees may not contain symbolic links")
        if path.is_file():
            result[str(path.relative_to(directory))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    if not result:
        raise ValueError("Empty snapshot")
    return result


def validate_history(messages):
    """Only complete, single-call boundaries can be restored."""
    pending = None
    seen = set()
    for message in messages:
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("Invalid message role")
        if pending:
            if role != "tool" or message.get("tool_call_id") != pending:
                raise ValueError("Unmatched tool result")
            pending = None
        elif role == "tool":
            raise ValueError("Orphan tool result")
        calls = message.get("tool_calls") or []
        if calls:
            if role != "assistant" or len(calls) != 1:
                raise ValueError("Exactly one call per assistant turn is supported")
            pending = calls[0]["id"]
            if not pending or pending in seen:
                raise ValueError("Duplicate or empty call id")
            seen.add(pending)
    if pending:
        raise ValueError("Cannot snapshot an unfinished tool call")
