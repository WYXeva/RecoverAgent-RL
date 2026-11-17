"""Native XML preserves typed text through the real nullable schema shape."""

import copy
import json

import pytest

from recoveragent.adapter import call_code
from recoveragent.v2_model import parse_native


def parse(value, schema, *, status="success"):
    tool = {
        "type": "function",
        "function": {
            "name": "supervisor__complete_task",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": schema,
                    "status": {"type": "string", "enum": ["success", "fail"]},
                },
                "required": ["status"],
                "additionalProperties": False,
            },
        },
    }
    before = copy.deepcopy(tool)
    text = (
        "<tool_call>\n<function=supervisor__complete_task>\n"
        f"<parameter=answer>\n{value}\n</parameter>\n"
        f"<parameter=status>\n{status}\n</parameter>\n"
        "</function>\n</tool_call><|im_end|>"
    )
    message = parse_native(text, "audit", "eos", [tool], {"tool_format": "qwen_xml"})
    assert tool == before
    fn = message["tool_calls"][0]["function"]
    return fn, [tool]


ANSWER = {
    "anyOf": [
        {"anyOf": [{"type": "number"}, {"type": "integer"}, {"type": "string"}]},
        {"type": "null"},
    ],
    "default": None,
}


@pytest.mark.parametrize(
    "value", ['{"cost": 12.5, "matches": true}', '["a", "b"]', "007", "  text  "]
)
def test_nested_nullable_answer_remains_exact_native_string(value):
    fn, tools = parse(value, ANSWER)
    _, args = call_code(fn["name"], fn["arguments"], tools)
    assert args["answer"] == value
    # The structured Teacher call and parsed native Student call agree.
    _, teacher = call_code(
        fn["name"], json.dumps({"answer": value, "status": "success"}), tools
    )
    assert args == teacher


@pytest.mark.parametrize("value", ["None", "null"])
def test_nested_nullable_none_still_decodes_as_null(value):
    fn, tools = parse(value, ANSWER)
    assert call_code(fn["name"], fn["arguments"], tools)[1]["answer"] is None


@pytest.mark.parametrize(
    "schema,value,expected",
    [
        ({"anyOf": [{"anyOf": [{"type": "boolean"}]}, {"type": "null"}]}, "True", True),
        ({"anyOf": [{"type": ["string", "null"]}]}, '{"a": 1}', '{"a": 1}'),
        ({"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}, "2", 2),
        (
            {
                "anyOf": [
                    {"type": "array", "items": {"type": "integer"}},
                    {"type": "null"},
                ]
            },
            "[1, 2]",
            [1, 2],
        ),
    ],
)
def test_nested_schema_keeps_other_parameter_types(schema, value, expected):
    fn, tools = parse(value, schema)
    assert call_code(fn["name"], fn["arguments"], tools)[1]["answer"] == expected


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "integer"}, "null"),
        ({"anyOf": [{"type": "boolean"}, {"type": "null"}]}, "wrong"),
        (
            {
                "anyOf": [
                    {"type": "integer", "minimum": 1, "maximum": 5},
                    {"type": "null"},
                ]
            },
            "6",
        ),
        (
            {"anyOf": [{"type": "string", "enum": ["one", "two"]}, {"type": "null"}]},
            "three",
        ),
        (
            {
                "type": "object",
                "required": ["a"],
                "properties": {"a": {"type": "integer"}},
                "additionalProperties": False,
            },
            '{"a": "bad"}',
        ),
    ],
)
def test_parser_fix_does_not_relax_strict_schema(schema, value):
    fn, tools = parse(value, schema)
    with pytest.raises(Exception):
        call_code(fn["name"], fn["arguments"], tools)
