"""Public, typed multi-value answers shared by Teacher and Student validation."""

import copy
import json
import math
import re

from .io import canonical, digest

MARKER = "\nPublic answer schema v1: "
KINDS = {"number", "integer", "string", "boolean"}


def validate_fields(fields):
    if (
        not isinstance(fields, dict)
        or not 1 <= len(fields) <= 16
        or any(
            not isinstance(k, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", k) is None
            or not isinstance(v, str)
            or v not in KINDS
            for k, v in fields.items()
        )
    ):
        raise ValueError("Invalid public multi-value answer fields")
    return fields


def prepare_proposal(proposal):
    prepared = copy.deepcopy(proposal)
    query = prepared["query"].strip()
    fields = prepared.get("answer_fields")
    # This exact public request names all three values. No hidden/reference
    # answer is used to infer their names or types.
    if fields is None and re.search(
        r"\b(?:tell me|report|return|give me)\s+(?:the\s+)?cart(?:'s)?\s+"
        r"base[ _]cost,?\s+tax,?\s+(?:and\s+)?total[ _]cost[.!]?\s*$",
        query,
        re.IGNORECASE,
    ):
        fields = {"base_cost": "number", "tax": "number", "total_cost": "number"}
    if fields is not None:
        validate_fields(fields)
        if MARKER in query:
            raise ValueError("Public answer format already supplied")
        prepared["answer_fields"] = fields
        prepared["query"] = (
            query
            + (
                "\n\nSubmit the requested values as a JSON object encoded in the answer string, "
                "with exactly the fields and value types below. Numeric fields must be JSON "
                "numbers without units, currency symbols, or thousands separators."
            )
            + MARKER
            + canonical(fields)
        )
    return prepared


def comparison(instruction):
    if MARKER not in instruction:
        return None
    if instruction.count(MARKER) != 1:
        raise ValueError("Ambiguous public answer schema")
    fields = validate_fields(json.loads(instruction.split(MARKER)[1]))
    return {
        "kind": "public_object_v1",
        "fields": fields,
        "instruction_sha256": digest(instruction),
        "rule": "explicit_public_answer_fields_v1",
    }


def object_answer(value, fields):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("Duplicate answer field")
            result[key] = item
        return result

    if isinstance(value, str):
        try:
            value = json.loads(value, object_pairs_hook=pairs)
        except (ValueError, TypeError):
            return None
    if not isinstance(value, dict) or set(value) != set(fields):
        return None
    for key, kind in fields.items():
        v = value[key]
        valid = {
            "number": type(v) is int or (type(v) is float and math.isfinite(v)),
            "integer": type(v) is int,
            "string": isinstance(v, str),
            "boolean": type(v) is bool,
        }[kind]
        if not valid:
            return None
    return value


def matches(actual, expected, fields):
    left, right = object_answer(actual, fields), object_answer(expected, fields)
    if left is None or right is None:
        return False
    from .answer_guard import numeric_representation

    return all(
        numeric_representation(left[k]) == numeric_representation(right[k])
        if kind in {"number", "integer"}
        else left[k] == right[k]
        for k, kind in fields.items()
    )
