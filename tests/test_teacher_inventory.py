"""Software-only transformation and provenance tests; no model/environment calls."""

import copy
import json

import pytest

from recoveragent import teacher_inventory as inventory
from recoveragent.io import digest


@pytest.mark.parametrize(
    "source, expected",
    [
        (' { "a" : [ 1, 2 ], "b" : true }\n', '{"a":[1,2],"b":true}'),
        (
            '{"text": " a  b\\n c ", "path": "x/y"}',
            '{"text":" a  b\\n c ","path":"x/y"}',
        ),
        (r'{ "escaped": "a\" b\\ c" }', r'{"escaped":"a\" b\\ c"}'),
        (
            '{ "n": 1.234567890123456789, "z": -0.00, "n": 1e+300 }',
            '{"n":1.234567890123456789,"z":-0.00,"n":1e+300}',
        ),
        ('{ "unicode": "中 文\\u0020" }', '{"unicode":"中 文\\u0020"}'),
        (' {"broken": ', ' {"broken": '),
        (' {"bad": NaN}', ' {"bad": NaN}'),
        ("  plain text  ", "  plain text  "),
    ],
)
def test_compact_preserves_strings_numbers_keys_and_invalid_text(source, expected):
    assert inventory.compact_json(source) == expected
    assert inventory.compact_json(expected) == expected


def test_only_tool_content_changes_no_call_or_constraint_removed():
    messages = [
        {"role": "system", "content": ' { "keep": "exact" } '},
        {"role": "user", "content": "Preserve exact content and all constraints."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "a",
                    "function": {"name": "app__read", "arguments": '{ "id": 1 }'},
                }
            ],
        },
        {
            "role": "tool",
            "content": ' { "id": 1, "error": " 401 " } ',
            "name": "app__read",
            "tool_call_id": "a",
        },
    ]
    before = copy.deepcopy(messages)
    result = inventory.compact_messages(messages)
    assert messages == before and result[:3] == messages[:3]
    assert result[3] == dict(messages[3], content='{"id":1,"error":" 401 "}')
    assert json.loads(result[3]["content"]) == json.loads(messages[3]["content"])


def test_renderer_keeps_current_schema_and_never_mutates_source():
    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return messages, kwargs

    messages = [{"role": "tool", "content": ' { "id": 1 } '}]
    tools = [{"function": {"name": "current__only"}}]
    rendered, kwargs = inventory.CompactTokenizer(Tokenizer()).apply_chat_template(
        messages, tools=tools, enable_thinking=False, add_generation_prompt=True
    )
    assert kwargs["tools"] is tools and kwargs["enable_thinking"] is False
    assert messages[0]["content"] == ' { "id": 1 } '
    assert rendered[0]["content"] == '{"id":1}'


def test_record_serialization_preserves_nested_schema_order_for_token_replay():
    record = {
        "tools": [
            {
                "function": {
                    "name": "a__b",
                    "description": "d",
                    "parameters": {
                        "properties": {
                            "z": {"type": "string"},
                            "a": {"type": "integer"},
                        }
                    },
                }
            }
        ]
    }
    restored = json.loads(inventory.record_line(record))
    assert list(restored["tools"][0]["function"]) == [
        "name",
        "description",
        "parameters",
    ]
    assert list(restored["tools"][0]["function"]["parameters"]["properties"]) == [
        "z",
        "a",
    ]
    assert inventory.record_line(restored) == inventory.record_line(record)


def fixture():
    messages = [
        {"role": "system", "content": "constraints"},
        {"role": "user", "content": "original"},
        {"role": "tool", "content": ' { "x": 1 } '},
        {"role": "assistant", "content": "done"},
    ]
    target = list(map(ord, "done<|im_end|>\n"))
    example = {
        "input_ids": [88] * 4 + target,
        "labels": [-100] * 4 + target,
        "loss_mask": [0] * 4 + [1] * len(target),
        "attention_mask": [1] * (4 + len(target)),
        "target_start": 4,
        "assistant_index": 3,
        "history_hash": digest(messages[:3]),
        "tools_hash": digest([]),
    }
    return {
        "messages": messages,
        "turns": [{"assistant_index": 3, "tools": []}],
    }, example


class Tokenizer:
    pad_token_id = 0

    def decode(self, ids):
        return "".join(map(chr, ids))


def test_derived_history_has_distinct_hash_original_target_and_mask(monkeypatch):
    record, example = fixture()
    before = copy.deepcopy(record)
    monkeypatch.setattr(
        inventory, "assistant_examples", lambda *a, **k: [copy.deepcopy(example)]
    )
    row = inventory.encode(record, Tokenizer(), compact=True)[0]
    assert record == before and row["input_ids"] == example["input_ids"]
    assert row["source_history_hash"] == example["history_hash"]
    assert row["history_hash"] == digest(
        inventory.compact_messages(record["messages"][:3])
    )
    assert row["history_hash"] != row["source_history_hash"]
    assert row["representation"] == inventory.COMPACT
    assert row["labels"][:4] == [-100] * 4


@pytest.mark.parametrize("damage", ["mask", "schema", "target", "missing"])
def test_reject_bad_mask_future_schema_modified_target_or_missing_turn(
    monkeypatch, damage
):
    record, example = fixture()
    rows = [example]
    if damage == "mask":
        example["labels"][0] = 88
    elif damage == "schema":
        example["tools_hash"] = digest(["future"])
    elif damage == "target":
        record["messages"][-1]["content"] = "different"
    else:
        rows = []
    monkeypatch.setattr(inventory, "assistant_examples", lambda *a, **k: rows)
    with pytest.raises(ValueError):
        inventory.encode(record, Tokenizer())


def test_overlength_defers_whole_trajectory_and_output_budget():
    record, row = fixture()
    row["input_ids"] = [1] * 32769
    assert not inventory.fits(inventory.measures([row]))
    row["input_ids"] = [1] * 4101
    assert not inventory.fits(inventory.measures([row]))


def test_plan_duplicates_and_anchor_corruption_rejected(tmp_path):
    anchor = tmp_path / "anchor"
    anchor.write_text("immutable")
    plan = {
        "format": inventory.FORMAT,
        "sources": [{"task_id": "a"}],
        "anchors": [{"path": str(anchor), "sha256": inventory.sha256(anchor)}],
    }
    plan["manifest_sha256"] = digest(plan)
    inventory.validate_plan(plan)
    anchor.write_text("changed")
    with pytest.raises(ValueError, match="anchor"):
        inventory.validate_plan(plan)
    plan["sources"].append({"task_id": "a"})
    plan["manifest_sha256"] = digest(
        {k: v for k, v in plan.items() if k != "manifest_sha256"}
    )
    with pytest.raises(ValueError, match="Duplicate"):
        inventory.validate_plan(plan)


def test_changed_review_refused_before_any_source_read(tmp_path):
    home = tmp_path / "review"
    home.mkdir()
    (home / "review.json").write_text("{}")
    with pytest.raises(ValueError, match="review changed"):
        inventory.load_source(
            {
                "review": str(home),
                "source": str(tmp_path / "missing"),
                "review_sha256": "wrong",
            }
        )


def test_transfer_refuses_wrong_trusted_hash_or_changed_files(tmp_path):
    from scripts.verify_teacher_inventory import verify

    (tmp_path / "manifest.json").write_text('{"files":{}}')
    (tmp_path / "manifest.sha256").write_text("untrusted")
    with pytest.raises(ValueError, match="Trusted manifest"):
        verify(tmp_path, "wrong")
    trusted = inventory.sha256(tmp_path / "manifest.json")
    (tmp_path / "unexpected.txt").write_text("extra")
    with pytest.raises(ValueError, match="checksum"):
        verify(tmp_path, trusted)
