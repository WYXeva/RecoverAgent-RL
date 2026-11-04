"""Opt-in nullable contract restoration from pinned, public OpenAPI definitions.

No task, model, database, signature eval, or third-party file mutation is needed.
Defaults/descriptions are annotations, never evidence that a parameter accepts null.
"""

import copy
import hashlib
import importlib.metadata
import re
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from . import APPWORLD_REVISION
from .io import canonical, digest, write_json

SCHEMA_REVISION = "schema_nullable_compatibility_v2"
CONFIG_KEY = "schema_compatibility_revision"
_TYPES = {"null", "boolean", "string", "integer", "number", "array", "object"}


class SchemaCompatibilityError(ValueError):
    """Unproven/ambiguous contracts stop preparation, without a legacy fallback."""


def _resolve(value, document, active=()):
    """Resolve local JSON pointers only; never fetch references or infer types."""
    if isinstance(value, list):
        return [_resolve(v, document, active) for v in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        ref = value["$ref"]
        if (
            not isinstance(ref, str)
            or not ref.startswith("#/")
            or ref in active
            or len(value) != 1
        ):
            raise SchemaCompatibilityError("Unsupported, external or cyclic reference")
        target = document
        try:
            for token in ref[2:].split("/"):
                target = target[token.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise SchemaCompatibilityError(
                "Unresolved public schema reference"
            ) from None
        return _resolve(target, document, (*active, ref))
    return {k: _resolve(v, document, active) for k, v in value.items()}


def _explicit_nullable(schema):
    """Understand the pinned typed/anyOf representation; reject opaque declarations."""
    if not isinstance(schema, dict) or any(
        k in schema for k in ("$ref", "nullable", "oneOf", "allOf", "not", "if")
    ):
        raise SchemaCompatibilityError("Unsupported public type declaration")
    Draft202012Validator.check_schema(schema)
    if "anyOf" in schema:
        # Evaluate every branch: a preceding null branch cannot hide an opaque type.
        branches = [_explicit_nullable(branch) for branch in schema["anyOf"]]
        nullable = any(branches)
    else:
        kind = schema.get("type")
        kinds = kind if isinstance(kind, list) else [kind]
        if not kinds or any(k not in _TYPES for k in kinds):
            raise SchemaCompatibilityError("Unresolved public type declaration")
        nullable = "null" in kinds
    return nullable and Draft202012Validator(schema).is_valid(None)


def _public_operations(app, document):
    if document.get("openapi") != "3.1.0":
        raise SchemaCompatibilityError("Only pinned OpenAPI 3.1.0 is supported")
    operations = {}
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                raise SchemaCompatibilityError("Unsupported public operation layout")
            description = operation.get("description", "")
            if "No-API-Docs" in description or "Private-API" in description:
                continue
            name = app + "__" + "_".join(operation["summary"].lower().split())
            if name in operations:
                raise SchemaCompatibilityError("Ambiguous public function name")
            properties = {}
            body = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            if body is not None:
                body = _resolve(body, document)
                if body.get("type") != "object" or "properties" not in body:
                    raise SchemaCompatibilityError("Unsupported public request body")
                properties.update(body["properties"])
            for parameter in operation.get("parameters", []):
                parameter = _resolve(parameter, document)
                key = parameter["name"]
                if key in properties or "schema" not in parameter:
                    raise SchemaCompatibilityError("Ambiguous public parameter")
                properties[key] = _resolve(parameter["schema"], document)
            operations[name] = {
                "path": path,
                "method": method,
                "properties": properties,
                "security": operation.get("security"),
            }
    return operations


def _removed_null_schema(original, required):
    """Reproduce only the pinned converter's lossy step, to reject source drift."""
    expected = copy.deepcopy(original)
    branches = expected.get("anyOf")
    if not isinstance(branches, list) or {"type": "null"} not in branches:
        raise SchemaCompatibilityError("Nullable declaration differs from pinned shape")
    branches.remove({"type": "null"})
    if len(branches) == 1:
        expected.update(expected.pop("anyOf")[0])
    expected["default"] = None
    expected.pop("title", None)
    if required:
        # The original converter cannot express this consistently. Do not drop required.
        expected.pop("default", None)
    return expected


def convert_catalogue(upstream, openapi_by_app, *, appworld_revision):
    """Return a complete audit receipt; both input objects remain unchanged."""
    if appworld_revision != APPWORLD_REVISION:
        raise SchemaCompatibilityError("Unpinned AppWorld revision")
    canonical(upstream)
    canonical(openapi_by_app)
    tools = copy.deepcopy(upstream)
    operations = {}
    for app, document in openapi_by_app.items():
        if app == "admin" or not re.fullmatch(r"[a-z][a-z0-9_]*", app):
            raise SchemaCompatibilityError("Non-public app")
        operations.update(_public_operations(app, document))
    changes, seen = [], set()
    for tool in tools:
        name = tool["function"]["name"]
        if name in seen or name not in operations:
            raise SchemaCompatibilityError("Missing or duplicate public function")
        seen.add(name)
        parameters = tool["function"]["parameters"]
        Draft202012Validator.check_schema(parameters)
        if (
            parameters.get("type") != "object"
            or parameters.get("additionalProperties", False) is not False
        ):
            raise SchemaCompatibilityError(
                "Schema conflicts with strict argument policy"
            )
        # Expose the existing call_code no-extra-arguments rule to the model too.
        parameters["additionalProperties"] = False
        operation = operations[name]
        for field, schema in parameters["properties"].items():
            original = operation["properties"].get(field)
            if original is None:
                # Pinned upstream synthesizes these authentication fields. Never relax them.
                generated_auth = (
                    field == "access_token"
                    and (operation["security"] is not None or name.endswith("__logout"))
                ) or (
                    operation["path"] == "/auth/token"
                    and field in {"username", "password"}
                )
                if not generated_auth or schema.get("type") != "string":
                    raise SchemaCompatibilityError(
                        "Parameter lacks a public type source"
                    )
                if Draft202012Validator(schema).is_valid(None):
                    raise SchemaCompatibilityError("Authentication schema accepts null")
                continue
            nullable = _explicit_nullable(original)
            _explicit_nullable(
                schema
            )  # Opaque upstream schemas must not become permissive.
            accepts_null = Draft202012Validator(schema).is_valid(None)
            if not nullable and accepts_null:
                raise SchemaCompatibilityError(
                    "Schema already widens a non-nullable type"
                )
            if not nullable or accepts_null:
                continue
            expected = _removed_null_schema(
                original, field in parameters.get("required", [])
            )
            if expected != schema:
                raise SchemaCompatibilityError(
                    "Upstream/public parameter schema mismatch"
                )
            # Preserve all constraints in the non-null branch. Keep annotations
            # outside it, once, so the visible schema doesn't duplicate long docs.
            non_null = copy.deepcopy(schema)
            annotations = {
                k: non_null.pop(k) for k in ("description", "default") if k in non_null
            }
            converted = {"anyOf": [non_null, {"type": "null"}], **annotations}
            parameters["properties"][field] = converted
            changes.append(
                {
                    "function": name,
                    "parameter": field,
                    "public_path": operation["path"],
                    "public_method": operation["method"],
                    "public_parameter_schema": original,
                    "public_parameter_sha256": digest(original),
                    "upstream_parameter_sha256": digest(schema),
                    "compatible_parameter_sha256": digest(converted),
                }
            )
        Draft202012Validator.check_schema(parameters)
    return {
        "revision": SCHEMA_REVISION,
        "appworld_revision": appworld_revision,
        "hash_encoding": "SHA-256 of recoveragent.io.canonical UTF-8 JSON",
        "upstream_catalogue": copy.deepcopy(upstream),
        "compatible_catalogue": tools,
        "upstream_sha256": digest(upstream),
        "compatible_sha256": digest(tools),
        "public_openapi_sha256": {app: digest(d) for app, d in openapi_by_app.items()},
        "changes": changes,
        "strict_top_level_additional_properties": False,
    }


def load_public_openapi(catalogue):
    """Read API definitions only, without creating AppWorld or loading a task."""
    import json
    from appworld.common.imports import import_apis_module

    direct = json.loads(
        importlib.metadata.distribution("appworld").read_text("direct_url.json") or "{}"
    )
    if urlsplit(direct.get("url", "")).path.rsplit("/", 1)[-1] != APPWORLD_REVISION:
        raise SchemaCompatibilityError(
            "Public API source is not the pinned installation"
        )
    documents, sources = {}, {}
    for app in sorted({t["function"]["name"].split("__")[0] for t in catalogue}):
        if app == "admin" or not re.fullmatch(r"[a-z][a-z0-9_]*", app):
            raise SchemaCompatibilityError("Non-public app")
        module = import_apis_module(app)
        documents[app] = copy.deepcopy(module.app.openapi())
        sources[app] = {
            "module": module.__name__,
            "source_sha256": hashlib.sha256(
                Path(module.__file__).read_bytes()
            ).hexdigest(),
        }
    return documents, {"installation": direct, "public_api_sources": sources}


def prepare_adapter_schemas(adapter, revision, output):
    """Shared Teacher/Student opt-in boundary, called before tool selection."""
    if revision is None:
        return None  # Historical configurations preserve byte-identical schemas.
    if revision != SCHEMA_REVISION:
        raise SchemaCompatibilityError("Unknown schema compatibility revision")
    if hasattr(adapter, "schema_compatibility"):
        raise SchemaCompatibilityError("Schema compatibility already configured")
    if adapter.versions.get("revision") != APPWORLD_REVISION:
        raise SchemaCompatibilityError(
            "Adapter is not using the pinned AppWorld revision"
        )
    documents, source = load_public_openapi(adapter.catalogue)
    audit = convert_catalogue(
        adapter.catalogue, documents, appworld_revision=APPWORLD_REVISION
    )
    audit["source"] = source
    audit["public_openapi"] = documents
    audit["implementation_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    # Persist all evidence before making the new catalogue available to either actor.
    write_json(Path(output) / "schema_compatibility.json", audit)
    adapter.upstream_catalogue = copy.deepcopy(audit["upstream_catalogue"])
    adapter.catalogue = copy.deepcopy(audit["compatible_catalogue"])
    adapter.schema_compatibility = audit
    adapter.versions = dict(
        adapter.versions,
        schema_compatibility={
            "revision": revision,
            "upstream_sha256": audit["upstream_sha256"],
            "compatible_sha256": audit["compatible_sha256"],
            "audit_sha256": digest(audit),
        },
    )
    return adapter.versions["schema_compatibility"]
