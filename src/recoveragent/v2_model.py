"""Pinned native text-only model/template bridge; no legacy adapter conversion."""

import copy
import hashlib
import json
import math
import re

from .io import canonical


def validate_identity(identity):
    from .io import read_json
    from .v2_tasks import ROOT

    choices = read_json(ROOT / "configs/v2_round1_coevolve_quality_20261109.json")[
        "models"
    ]
    if identity not in choices.values():
        raise ValueError("Unpinned v2 model/template")
    return identity


class NativeTokenizer:
    """Native Qwen3.5 expects argument dictionaries, Qwen3 accepts them too.

    This changes only the template input representation, never argument values.
    Raw structured messages are retained separately and remain immutable.
    """

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def __getattr__(self, name):
        return getattr(self.tokenizer, name)

    def __call__(self, *args, **kwargs):
        return self.tokenizer(*args, **kwargs)

    def apply_chat_template(self, messages, **kwargs):
        messages = copy.deepcopy(messages)
        for message in messages:
            for call in message.get("tool_calls", []):
                value = call["function"]["arguments"]
                if isinstance(value, str):
                    call["function"]["arguments"] = json.loads(value)
        return self.tokenizer.apply_chat_template(messages, **kwargs)


def load_tokenizer(identity, *, local_files_only=True):
    from transformers import AutoTokenizer

    validate_identity(identity)
    tokenizer = AutoTokenizer.from_pretrained(
        identity["model"],
        revision=identity["revision"],
        local_files_only=local_files_only,
    )
    if (
        hashlib.sha256(tokenizer.chat_template.encode()).hexdigest()
        != identity["chat_template_sha256"]
    ):
        raise ValueError("Native chat template changed")
    return NativeTokenizer(tokenizer)


def _schema_types(schema):
    """Read native parameter types through nullable anyOf wrappers."""
    kinds = schema.get("type", [])
    types = {kinds} if isinstance(kinds, str) else set(kinds)
    for branch in schema.get("anyOf", []):
        types.update(_schema_types(branch))
    return types


class ParserContractError(ValueError):
    """A compliant raw answer cannot be represented by the advertised interface."""


def verify_native_answer(raw, parsed, schema, contract):
    """Audit conversion against the actual XML + public types, never old prose.

    This does not repair the call: a demonstrated parser conversion bug stops it.
    """
    from .completion_contract import scalar
    from jsonschema import Draft202012Validator

    raw = raw.removeprefix("\n").removesuffix("\n")
    types = _schema_types(schema)
    if scalar(contract) and re.fullmatch(
        r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", raw.strip()
    ):
        value = json.loads(raw)
        if (type(value) is int or math.isfinite(value)) and Draft202012Validator(
            contract["schema"]
        ).is_valid(value):
            if type(parsed) not in (int, float) or parsed != value:
                raise ParserContractError(
                    "Parser changed a compliant raw numeric answer"
                )
            return
    explicit_text = (
        contract
        and contract["encoding"] == "value"
        and contract["schema"]["type"] == "string"
    )
    if (
        "string" in types
        and (raw not in {"null", "None"} or explicit_text)
        and parsed != raw
    ):
        raise ParserContractError("Parser changed schema-allowed answer text")


def _parameter(text, schema, *, answer_contract=None):
    # Delimiters add exactly one newline on each side; preserve string whitespace.
    if text.startswith("\n"):
        text = text[1:]
    if text.endswith("\n"):
        text = text[:-1]
    types = _schema_types(schema)
    from .completion_contract import scalar

    if (
        answer_contract
        and answer_contract["encoding"] == "value"
        and answer_contract["schema"]["type"] == "string"
        and "string" in types
    ):
        return text
    if scalar(answer_contract) and re.fullmatch(
        r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", text.strip()
    ):
        number = json.loads(text)
        if type(number) is int or math.isfinite(number):
            from jsonschema import Draft202012Validator

            if Draft202012Validator(answer_contract["schema"]).is_valid(number):
                if not types.intersection({"number", "integer"}):
                    raise ParserContractError(
                        "Public numeric answer conflicts with tool Schema"
                    )
                return number
    if answer_contract and answer_contract["encoding"] == "json_string":
        from .completion_contract import decode
        from jsonschema import ValidationError

        try:
            decode(text, answer_contract)
        except (ValueError, TypeError, ValidationError):
            pass  # A real model format error, handled by the shared submit check.
        else:
            if "string" not in types:
                raise ParserContractError(
                    "Public JSON-text answer conflicts with tool Schema"
                )
    if types == {"string"} or ("string" in types and text not in {"null", "None"}):
        return text
    if text in {"None", "null"} and "null" in types:
        return None
    if text in {"True", "False"} and "boolean" in types:
        return text == "True"
    from .harness import json_object

    def nonfinite(_):
        raise ValueError("Nonfinite argument")

    try:
        return json.loads(text, object_pairs_hook=json_object, parse_constant=nonfinite)
    except json.JSONDecodeError:
        return text  # Shared JSON Schema validation will reject a wrong scalar type.


def parse_native(
    text,
    call_prefix,
    finish_reason,
    tools,
    identity,
    *,
    answer_contract=None,
    audit_conversion=False,
):
    from .student import parse_batch

    if identity["tool_format"] == "qwen_json":
        return parse_batch(text, call_prefix, finish_reason)
    if finish_reason not in {"stop", "eos"}:
        raise ValueError("Incomplete generation; reject entire batch")
    text = text.rstrip()
    if text.endswith("<|im_end|>"):
        text = text[: -len("<|im_end|>")]
    if any(t in text for t in ("<|im_start|>", "<|im_end|>", "<think>")):
        raise ValueError("Unexpected message/thinking boundary")
    start = text.find("<tool_call>")
    if start < 0:
        if "</tool_call>" in text:
            raise ValueError("Unmatched tool closing tag")
        return {"role": "assistant", "content": text.rstrip()}
    content, tail, calls = text[:start].rstrip(), text[start:], []
    catalogue = {t["function"]["name"]: t["function"] for t in tools}
    while tail.strip():
        match = re.match(
            r"\s*<tool_call>\s*<function=([A-Za-z0-9_]+)>(.*?)</function>\s*</tool_call>",
            tail,
            re.DOTALL,
        )
        if not match:
            raise ValueError("Malformed native tool call")
        name, body = match.groups()
        properties = catalogue.get(name, {}).get("parameters", {}).get("properties", {})
        arguments = {}
        while body.strip():
            parameter = re.match(
                r"\s*<parameter=([A-Za-z0-9_]+)>(.*?)</parameter>", body, re.DOTALL
            )
            if not parameter:
                raise ValueError("Malformed native parameter")
            key, value = parameter.groups()
            if key in arguments or "<parameter=" in value or "<tool_call>" in value:
                raise ValueError("Duplicate or ambiguous native parameter")
            arguments[key] = _parameter(
                value,
                properties.get(key, {}),
                answer_contract=answer_contract
                if name == "supervisor__complete_task" and key == "answer"
                else None,
            )
            if (
                key == "answer"
                and name == "supervisor__complete_task"
                and (answer_contract is not None or audit_conversion)
            ):
                verify_native_answer(
                    value, arguments[key], properties.get(key, {}), answer_contract
                )
            body = body[parameter.end() :]
        calls.append(
            {
                "id": f"{call_prefix}_{len(calls)}",
                "type": "function",
                "function": {"name": name, "arguments": canonical(arguments)},
            }
        )
        tail = tail[match.end() :]
    return {"role": "assistant", "content": content, "tool_calls": calls}


def load_base(identity, *, device="cuda"):
    import torch
    from transformers import AutoModelForCausalLM, AutoModelForImageTextToText

    validate_identity(identity)
    if identity["tool_format"] == "qwen_xml":
        verify_weights(identity)
    loader = (
        AutoModelForImageTextToText
        if identity["tool_format"] == "qwen_xml"
        else AutoModelForCausalLM
    )
    return loader.from_pretrained(
        identity["model"],
        revision=identity["revision"],
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    ).to(device)


def verify_weights(identity):
    from huggingface_hub import try_to_load_from_cache
    from .io import read_json
    from .v2_tasks import ROOT

    expected = read_json(ROOT / "configs/v2_qwen35_weights.json")
    if (expected["model"], expected["revision"]) != (
        identity["model"],
        identity["revision"],
    ):
        raise ValueError("Unpinned model weight manifest")
    for name, checksum in expected["files"].items():
        path = try_to_load_from_cache(
            identity["model"], name, revision=identity["revision"]
        )
        if not isinstance(path, str):
            raise ValueError("Pinned base weight is not cached: " + name)
        h = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
                h.update(chunk)
        if h.hexdigest() != checksum:
            raise ValueError("Base weight checksum mismatch: " + name)
    return expected
