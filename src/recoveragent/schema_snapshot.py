"""Rebuild a snapshot's public schema contract before importing any state.

Conversion remains exclusively in schema_compatibility. Snapshot expectations
are immutable; an unsupported revision or different catalogue fails closed.
"""

from .io import digest
from .schema_compatibility import SCHEMA_REVISION, SchemaCompatibilityError

RECORDED_REVISION = object()


def snapshot_revision(manifest):
    versions = manifest["versions"]
    if "schema_compatibility" not in versions:
        return None
    binding = versions["schema_compatibility"]
    if not isinstance(binding, dict) or binding.get("revision") != SCHEMA_REVISION:
        raise SchemaCompatibilityError("Unsupported snapshot Schema revision")
    if any(
        not isinstance(binding.get(key), str)
        or len(binding[key]) != 64
        or any(c not in "0123456789abcdef" for c in binding[key])
        for key in ("upstream_sha256", "compatible_sha256", "audit_sha256")
    ):
        raise SchemaCompatibilityError("Invalid snapshot Schema hash binding")
    return binding["revision"]


def check_schema_binding(adapter, versions, tools):
    """Validate live catalogue, audit and visible tools, including undiscovered APIs."""
    expected = versions.get("schema_compatibility")
    actual = adapter.versions.get("schema_compatibility")
    if (
        "schema_compatibility" not in versions
        and "schema_compatibility" not in adapter.versions
    ):
        return  # Preserve the original legacy restore contract.
    snapshot_revision({"versions": versions})
    snapshot_revision({"versions": adapter.versions})
    if expected is None or actual != expected:
        raise SchemaCompatibilityError("Snapshot Schema version/hash binding mismatch")
    audit = getattr(adapter, "schema_compatibility", None)
    if (
        not isinstance(audit, dict)
        or digest(audit) != expected["audit_sha256"]
        or digest(adapter.catalogue) != expected["compatible_sha256"]
        or digest(adapter.upstream_catalogue) != expected["upstream_sha256"]
        or audit["compatible_catalogue"] != adapter.catalogue
        or audit["upstream_catalogue"] != adapter.upstream_catalogue
    ):
        raise SchemaCompatibilityError("Snapshot Schema catalogue/audit hash mismatch")
    public = {t["function"]["name"]: t for t in adapter.catalogue}
    names = [t["function"]["name"] for t in tools]
    if len(names) != len(set(names)) or any(
        t != public.get(t["function"]["name"])
        or t["function"]["name"].startswith("admin__")
        for t in tools
    ):
        raise SchemaCompatibilityError("Snapshot visible tools differ from catalogue")


def prepare_snapshot_adapter(
    adapter, manifest, context, output, *, expected_revision=RECORDED_REVISION
):
    revision = snapshot_revision(manifest)
    if expected_revision is not RECORDED_REVISION and revision != expected_revision:
        raise SchemaCompatibilityError(
            "Configured and snapshot Schema revisions differ"
        )
    if revision is not None and "schema_compatibility" not in adapter.versions:
        adapter.configure_schema_compatibility(revision, output)
    check_schema_binding(adapter, manifest["versions"], context["tools"])
