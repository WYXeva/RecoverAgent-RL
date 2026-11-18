"""Opt-in public API search and deterministic documentation projection."""

import copy
import json
import re
from pathlib import Path

from .io import canonical, digest, write_json

REVISION = "public_tool_discovery_compact_v1"
KEY = "tool_discovery_revision"
SEARCH = "api_docs__search_apis"
NOTICE = (
    "Find APIs with api_docs__search_apis using an application and English keywords; "
    "page the results or use show_api_descriptions for the complete application list. "
    "Then call show_api_doc for the chosen API to make it callable. API argument "
    "types, defaults and descriptions are in the current tools schema. Documentation "
    "results retain return schemas and supplementary notes; parameter_schema_ref "
    "refers to that same visible schema. Search only finds public APIs; it performs "
    "no business action."
)


def search_tool(catalogue):
    apps = sorted({t["function"]["name"].split("__")[0] for t in catalogue})
    return {
        "type": "function",
        "function": {
            "name": SEARCH,
            "description": "Search public API names and descriptions. Read show_api_doc before calling a found API. Empty query lists APIs; offset pages matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "enum": apps},
                    "query": {"type": "string", "default": ""},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                    },
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                },
                "required": ["app_name"],
                "additionalProperties": False,
            },
        },
    }


def configure(adapter, revision, output):
    if revision is None:
        return
    if revision != REVISION or hasattr(adapter, "tool_discovery"):
        raise ValueError("Unknown or repeated tool discovery revision")
    tool = search_tool(adapter.catalogue)
    binding = {
        "revision": revision,
        "catalogue_sha256": digest(adapter.catalogue),
        "search_tool_sha256": digest(tool),
        "notice_sha256": digest(NOTICE),
    }
    write_json(Path(output) / "tool_discovery.json", {**binding, "search_tool": tool})
    adapter.tool_discovery = {
        "binding": binding,
        "tool": tool,
        "output": str(output),
        "calls": 0,
    }
    adapter.versions = {**adapter.versions, "tool_discovery": binding}


def extra_tools(adapter, versions=None):
    current = getattr(adapter, "versions", {})
    expected = (versions if versions is not None else current).get("tool_discovery")
    actual = current.get("tool_discovery")
    if expected != actual:
        raise ValueError("Snapshot tool discovery protocol differs")
    if actual is None:
        return []
    state = getattr(adapter, "tool_discovery", {})
    tool = search_tool(adapter.catalogue)
    if (
        actual.get("revision") != REVISION
        or actual != state.get("binding")
        or actual.get("catalogue_sha256") != digest(adapter.catalogue)
        or actual.get("search_tool_sha256") != digest(tool)
        or state.get("tool") != tool
        or actual.get("notice_sha256") != digest(NOTICE)
    ):
        raise ValueError("Tool discovery catalogue/hash mismatch")
    return [copy.deepcopy(tool)]


def search(catalogue, arguments):
    app, query = arguments["app_name"], arguments.get("query", "")
    words = set(re.findall(r"[a-z0-9]+", query.casefold()))
    matches = []
    for item in catalogue:
        f = item["function"]
        app_name, api_name = f["name"].split("__", 1)
        if app_name != app:
            continue
        haystack = (
            api_name.replace("_", " ") + " " + f.get("description", "")
        ).casefold()
        score = sum(word in haystack for word in words)
        if not words or score:
            matches.append((score, api_name, f.get("description", "")))
    matches.sort(key=lambda x: (-x[0], x[1]))
    offset, limit = arguments.get("offset", 0), arguments.get("limit", 5)
    end = offset + limit
    return canonical(
        {
            "app_name": app,
            "query": query,
            "total_matches": len(matches),
            "next_offset": end if end < len(matches) else None,
            "apis": [
                {"api_name": n, "description": d} for _, n, d in matches[offset:end]
            ],
        }
    )


def project_document(raw, function):
    """Keep return schemas and unknown notes; never summarize business observations."""
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if not isinstance(doc, dict) or not isinstance(doc.get("parameters"), list):
        return raw
    properties = function["parameters"].get("properties", {})
    notes = []
    for parameter in doc["parameters"]:
        if not isinstance(parameter, dict) or parameter.get("name") not in properties:
            return raw
        name = parameter["name"]
        note = {
            k: v
            for k, v in parameter.items()
            if k not in {"name", "type", "required", "default"}
        }
        if note.get("description") == properties[name].get("description"):
            note.pop("description", None)
        if note.get("constraints") == []:
            note.pop("constraints")
        if note:
            notes.append({"name": name, **note})
    compact = {k: v for k, v in doc.items() if k != "parameters"}
    if compact.get("description") == function.get("description"):
        compact.pop("description", None)
    compact["parameter_schema_ref"] = function["name"]
    if notes:
        compact["parameter_notes"] = notes
    return canonical(compact)


def document_result(adapter, name, arguments, raw, tools):
    state = getattr(adapter, "tool_discovery", None)
    if state is None or name != "api_docs__show_api_doc":
        return raw
    selected = f"{arguments.get('app_name', '').strip().lower()}__{arguments.get('api_name', '').strip().lower()}"
    function = next(
        (t["function"] for t in tools if t["function"]["name"] == selected), None
    )
    projected = raw if function is None else project_document(raw, function)
    state["calls"] += 1
    write_json(
        Path(state["output"]) / "tool_discovery_calls" / f"{state['calls']:06d}.json",
        {
            "revision": REVISION,
            "function": selected,
            "raw": raw,
            "visible": projected,
            "raw_sha256": digest(raw),
            "visible_sha256": digest(projected),
        },
    )
    return projected
