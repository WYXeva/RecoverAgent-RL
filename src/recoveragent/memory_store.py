"""Task-local exact evidence index; JSONL snapshots, no model or database access."""

import copy
import json
from pathlib import Path

from .io import canonical, digest

INDEX_VERSION = "egtm-evidence-jsonl-v1"


def json_value(text):
    try:
        value = json.loads(text)
        canonical(value)  # Reject non-finite values as well as non-JSON output.
        return value
    except (ValueError, TypeError):
        return None


def leaves(value, path=()):
    """Exact typed scalar values and JSON paths; dates/IDs/paths stay unchanged."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, (*path, index))
    else:
        yield list(path), value


class EvidenceIndex:
    def __init__(self, task_id):
        self.task_id = task_id
        self.rows = []
        self.keys = {k: {} for k in ("source_id", "tool_name", "field", "entity_id")}

    def append(self, row):
        if row["task_id"] != self.task_id or row["source_id"] in self.keys["source_id"]:
            raise ValueError("Duplicate or cross-task evidence")
        number = len(self.rows)
        self.rows.append(copy.deepcopy(row))
        keys = {
            "source_id": [row["source_id"]],
            "tool_name": [row["tool_name"]],
            "field": [],
            "entity_id": [],
        }
        for value in (row["arguments"], json_value(row["content"])):
            for path, scalar in leaves(value):
                field_path = [part for part in path if isinstance(part, str)]
                if not field_path:
                    continue
                key = field_path[-1]
                keys["field"].extend(field_path)
                if key == "id" or key.endswith(("_id", "_ids")):
                    keys["entity_id"].append(canonical(scalar))
        for kind, values in keys.items():
            for value in values:
                self.keys[kind].setdefault(value, set()).add(number)

    def search(self, *, task_id, as_of, limit=4, **filters):
        """AND exact filters. Always require a task identity and legal time cutoff."""
        if task_id != self.task_id or type(as_of) is not int or as_of < 0:
            raise ValueError("Invalid evidence task/time boundary")
        if set(filters) - self.keys.keys():
            raise ValueError("Unknown evidence search field")
        candidates = set(range(len(self.rows)))
        for kind, value in filters.items():
            key = canonical(value) if kind == "entity_id" else value
            candidates &= self.keys[kind].get(key, set())
        return [
            copy.deepcopy(self.rows[i])
            for i in sorted(candidates, reverse=True)
            if self.rows[i]["boundary_end"] <= as_of and self.rows[i]["executed"]
        ][:limit]

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            for row in self.rows:
                stream.write(canonical(row) + "\n")

    @property
    def identity(self):
        return {
            "version": INDEX_VERSION,
            "rows": len(self.rows),
            "sha256": digest(self.rows),
        }
