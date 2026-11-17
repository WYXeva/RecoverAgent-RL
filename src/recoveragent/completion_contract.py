"""Public output format shared by parsing, submission and generated-goal scoring.

This contains no answers. Historical instructions are never rewritten in place.
"""

import copy
import json
import re

from jsonschema import Draft202012Validator, ValidationError, SchemaError

from .io import canonical, digest

REVISION = "completion_submission_v2"
MARKER = "\nPublic completion contract v2: "
NOTICE = {
    "revision": REVISION,
    "rule": (
        "Follow the public output contract. For status=success provide answer when "
        "required; for a pure action without a requested report, answer may be null "
        "or omitted. status=fail never requires an answer. A completion format "
        "rejection was NOT executed. You have one correction reply, shared with "
        "numeric-format correction, containing only complete_task; no business "
        "tools in that reply. Preserve the original numeric value when correcting "
        "only its format. An exhausted correction ends this attempt."
    ),
}


def completion_attempt(raw):
    if isinstance(raw, str):
        return bool(
            re.search(
                r'<function\s*=\s*supervisor__complete_task\b|"name"\s*:\s*"supervisor__complete_task"',
                raw,
            )
        )
    if isinstance(raw, dict):
        return any(
            c.get("function", {}).get("name") == "supervisor__complete_task"
            for c in raw.get("tool_calls", [])
        )
    return False


def validate(contract):
    if set(contract) != {"required", "encoding", "schema"}:
        raise ValueError("Output contract needs required, encoding and schema")
    if type(contract["required"]) is not bool or contract["encoding"] not in {
        "value",
        "json_string",
    }:
        raise ValueError("Unsupported public output encoding")
    try:
        Draft202012Validator.check_schema(contract["schema"])
    except SchemaError as exc:
        raise ValueError("Invalid public output JSON Schema") from exc

    # No remote references and no hidden/coercive transforms.
    def walk(value):
        if isinstance(value, dict):
            if any(k in value for k in ("$ref", "$dynamicRef")):
                raise ValueError("Output schema references are unsupported")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(contract["schema"])
    if not isinstance(contract["schema"], dict) or "type" not in contract["schema"]:
        raise ValueError("Output schema needs an explicit type")
    return copy.deepcopy(contract)


def render(instruction, contract):
    contract = validate(contract)
    if MARKER in instruction:
        if from_instruction(instruction) != contract:
            raise ValueError("Conflicting public output contracts")
        return instruction
    return instruction + MARKER + canonical(contract)


def from_instruction(instruction):
    if MARKER in instruction:
        return validate(json.loads(instruction.split(MARKER, 1)[1]))
    from .coevolve_answers import comparison

    legacy = comparison(instruction)
    if legacy is not None:
        return {
            "required": True,
            "encoding": "json_string",
            "schema": {
                "type": "object",
                "properties": {k: {"type": v} for k, v in legacy["fields"].items()},
                "required": list(legacy["fields"]),
                "additionalProperties": False,
            },
        }
    from .answer_guard import public_contract

    if public_contract(instruction)["kind"] in {"money", "number"}:
        return {"required": True, "encoding": "value", "schema": {"type": "number"}}
    return None  # No broad keyword ban on original-task nulls.


def scalar(contract):
    return bool(
        contract
        and contract["encoding"] == "value"
        and contract["schema"]["type"] in ("number", "integer")
    )


def decode(answer, contract):
    if contract["encoding"] == "json_string":
        if not isinstance(answer, str):
            raise ValueError("answer must be JSON encoded as a string")
        from .harness import json_object

        def reject(_):
            raise ValueError("Nonfinite answer")

        answer = json.loads(
            answer, object_pairs_hook=json_object, parse_constant=reject
        )
    Draft202012Validator(contract["schema"]).validate(answer)
    canonical(answer)  # Reject NaN/Infinity even in nested values.
    return answer


def issue(args, contract):
    if args.get("status", "success") != "success" or contract is None:
        return None
    value = args.get("answer")
    if value is None and not contract["required"]:
        return None
    try:
        decode(value, contract)
    except (ValueError, TypeError, ValidationError) as exc:
        # ValidationError is not a ValueError; report only public input/schema.
        return {
            "field": "answer",
            "expected": contract,
            "actual_type": type(value).__name__,
            "problem": str(exc).split("\n")[0],
        }
    return None


def identity(instruction):
    return {
        "revision": REVISION,
        "instruction_sha256": digest(instruction),
        "output": from_instruction(instruction),
    }


def schema_conflict(args, tools, contract):
    """Only a demonstrated public-contract/type contradiction is a system fault."""
    if (
        contract is None
        or args.get("status", "success") != "success"
        or issue(args, contract)
    ):
        return False
    from .v2_model import _schema_types

    schema = next(
        t["function"]["parameters"]
        for t in tools
        if t["function"]["name"] == "supervisor__complete_task"
    )
    types = _schema_types(schema.get("properties", {}).get("answer", {}))
    if scalar(contract):
        return not types.intersection({"number", "integer"})
    return contract["encoding"] == "json_string" and "string" not in types
