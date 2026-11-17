"""Controller-only model metadata and static state-path checks; never reads rows."""

import importlib
import importlib.metadata
import re

from . import APPWORLD_REVISION
from .coevolve_goal import ScoringGap


def model_schema(model):
    def compact(value, *, named=False):
        if isinstance(value, dict):
            return {
                k: compact(v, named=k in {"properties", "$defs", "patternProperties"})
                for k, v in value.items()
                if named or k not in {"title", "description", "default", "examples"}
            }
        if isinstance(value, list):
            return [compact(v) for v in value]
        return value

    return compact(model.model_json_schema())


def related_metadata(triplets):
    """Pinned source, related apps only. No world or database is constructed."""
    direct = importlib.metadata.distribution("appworld").read_text("direct_url.json")
    if not direct or APPWORLD_REVISION not in direct:
        raise ScoringGap("Contract metadata requires the pinned AppWorld revision")
    apps = {
        row["action"]["name"].split("__")[0]
        for row in triplets
        if "__" in row["action"]["name"]
    } - {"supervisor", "api_docs"}
    result = {}
    for app in sorted(apps):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", app):
            raise ScoringGap("Invalid metadata application")
        try:
            module = importlib.import_module(f"appworld.apps.{app}.models")
        except ImportError as exc:
            raise ScoringGap(f"Unavailable model metadata: {app}") from exc
        for name, model in module.SQLModel.names_and_models():
            result[f"{app}.{name}"] = model_schema(model)
    return {"appworld_revision": APPWORLD_REVISION, "tables": result}


def validate_fields(spec, metadata):
    """Infer only state expression shapes; validate paths even for empty tables."""
    registry = metadata["tables"]

    def path(shape, keys, root):
        if shape is None:
            return None  # Public answers/actions are not database model declarations.
        for key in keys:
            variants = shape.get("anyOf") or shape.get("oneOf")
            if variants:
                nonnull = [v for v in variants if v.get("type") != "null"]
                if len(nonnull) != 1:
                    raise ScoringGap("Ambiguous state field path")
                shape = nonnull[0]
            while "$ref" in shape:
                ref = shape["$ref"]
                if not ref.startswith("#/$defs/"):
                    raise ScoringGap(f"Unsupported state field reference: {ref}")
                shape = root.get("$defs", {}).get(ref.split("/")[-1])
                if shape is None:
                    raise ScoringGap(f"Unresolved state field reference: {ref}")
            variants = shape.get("anyOf") or shape.get("oneOf")
            if variants:
                nonnull = [v for v in variants if v.get("type") != "null"]
                if len(nonnull) != 1:
                    raise ScoringGap("Ambiguous state field path")
                shape = nonnull[0]
            if type(key) is int and key >= 0 and shape.get("type") == "array":
                shape = shape.get("items", {})
            elif isinstance(key, str) and shape.get("type") == "object":
                if key in shape.get("properties", {}):
                    shape = shape["properties"][key]
                elif isinstance(shape.get("additionalProperties"), dict):
                    shape = shape["additionalProperties"]
                else:
                    raise ScoringGap(f"Unavailable state field: {key}")
            else:
                raise ScoringGap(f"Invalid state field path component: {key!r}")
        return shape

    def walk(node, depth=0):
        if depth > 24:
            raise ScoringGap("Cyclic/over-deep goal bindings")
        op, arg = next(iter(node.items()))
        shape, root = None, {}
        if op == "binding":
            return walk(spec.get("bindings", {})[arg], depth + 1)
        if op == "rows":
            table = arg["table"]
            if table not in registry:
                raise ScoringGap(f"Unavailable objective table: {table}")
            root = registry[table]
            shape = {"type": "array", "items": root}
        elif op in {"get", "values", "filter"}:
            shape, root = walk(arg["from"], depth + 1)
            if op == "get":
                return path(shape, arg["path"], root), root
            if op == "values":
                item = path(shape, [0, *arg["path"]], root)
                return ({"type": "array", "items": item} if item else None), root
        elif op not in {"literal", "answer", "actions"}:
            child, root = walk(arg, depth + 1)
            if op == "flatten" and child is not None:
                return {"type": "array", "items": path(child, [0, 0], root)}, root
            return (child if op == "unique" else None), root
        if op in {"rows", "actions", "filter"}:
            for field, value in arg.get("where", {}).items():
                path(shape, [0, *field.split(".")], root)
                if isinstance(value, dict):
                    walk(next(iter(value.values())), depth + 1)
        return shape, root

    for node in spec.get("bindings", {}).values():
        walk(node)
    for check in spec["checks"]:
        walk(check["left"])
        walk(check["right"])
