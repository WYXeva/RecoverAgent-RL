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

    def retrieve(self, *, task_id, as_of, queries, present_ids=(), limit=3):
        """Select exact observed evidence; do not reinsert results already in input.

        `field` selects every matching JSON path. Use `path` to select one object;
        entity_id remains a row filter, not an inferred join across nested objects.
        """
        present = {
            r["source_id"]: r
            for r in self.rows
            if r["source_id"] in present_ids
            and r["boundary_end"] <= as_of
            and r["executed"]
        }
        equivalents = {
            digest([r["tool_name"], r["arguments"], r["content"]]): r["source_id"]
            for r in present.values()
        }
        output, seen = [], set()
        for query in queries:
            filters = {k: v for k, v in query.items() if k != "path"}
            if "source_id" in filters:
                # A known opaque result still has a source; inability to index a
                # field must not erase its reference or prevent verbatim fallback.
                filters.pop("field", None)
            for row in self.search(
                task_id=task_id, as_of=as_of, limit=limit, **filters
            ):
                if row["source_id"] in present:
                    continue
                packet = project(row, query)
                duplicate = equivalents.get(
                    digest([row["tool_name"], row["arguments"], row["content"]])
                )
                if duplicate and packet["representation"] == "raw":
                    packet.pop("content")
                    packet.update(
                        representation="reference", content_in_history=duplicate
                    )
                signature = digest({k: v for k, v in packet.items() if k != "query"})
                if signature not in seen:
                    seen.add(signature)
                    output.append(packet)
        return output

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


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Ambiguous JSON key")
        value[key] = item
    return value


def field_values(value, field, path=()):
    """Exact named fields, with full paths; no inferred entity-to-field binding."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == field:
                yield list((*path, key)), child
            yield from field_values(child, field, (*path, key))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from field_values(child, field, (*path, i))


def project(row, query):
    """Projection retains source identity. Opaque/ambiguous data stays verbatim."""
    import hashlib

    evidence = {
        "source_id": row["source_id"],
        "tool_name": row["tool_name"],
        "reference": {
            "tool_call_id": row["source_id"],
            "message_index": row["result_index"],
            "sha256": row["result_sha256"],
        },
        "content_sha256": hashlib.sha256(row["content"].encode()).hexdigest(),
        "query": copy.deepcopy(query),
    }
    if "path" in query or "field" in query:
        try:
            value = json.loads(row["content"], object_pairs_hook=_unique_object)
            canonical(value)
        except (ValueError, TypeError):
            evidence["projection_unavailable"] = "opaque_or_ambiguous_json"
        else:
            if "path" in query:
                child = value
                try:
                    for part in query["path"]:
                        if type(part) is str and isinstance(child, dict):
                            child = child[part]
                        elif (
                            type(part) is int and isinstance(child, list) and part >= 0
                        ):
                            child = child[part]
                        else:
                            raise KeyError("Invalid JSON path")
                except (KeyError, IndexError):
                    pairs = []
                else:
                    pairs = [(query["path"], child)]
            else:
                pairs = list(field_values(value, query["field"]))
            evidence.update(
                representation="projection",
                fields=[
                    {"path": path, "value": child, "value_sha256": digest(child)}
                    for path, child in pairs
                ],
            )
            return evidence
    evidence.update(
        representation="raw", content=row["content"], arguments=row["arguments"]
    )
    return evidence
