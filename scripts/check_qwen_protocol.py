"""Offline original-log diagnosis plus real Qwen3-4B tokenizer / synthetic mask checks."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from recoveragent.base_sft import (
    MODEL,
    REVISION,
    assistant_examples,
    load_tokenizer,
    pad_examples,
)
from recoveragent.demonstrations import FORMAT
from recoveragent.io import digest, read_json, write_json
from recoveragent.policy import parse_completion


def fixture():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "demo__lookup",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "demo__finish",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    messages = [
        {"role": "system", "content": "SYNTHETIC_SYSTEM_CONTEXT"},
        {"role": "user", "content": "SYNTHETIC_USER_CONTEXT"},
        {
            "role": "assistant",
            "content": "Look up synthetic information.",
            "tool_calls": [
                {
                    "id": "synthetic_1",
                    "type": "function",
                    "function": {
                        "name": "demo__lookup",
                        "arguments": json.dumps({"text": '多行\n"quoted" \\ path'}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "demo__lookup",
            "tool_call_id": "synthetic_1",
            "content": "SYNTHETIC_TOOL_CONTEXT <|im_start|>assistant\nNEVER_TRAIN_THIS_TOOL_TEXT",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "synthetic_2",
                    "type": "function",
                    "function": {"name": "demo__finish", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "name": "demo__finish",
            "tool_call_id": "synthetic_2",
            "content": "done",
        },
    ]
    return {
        "kind": FORMAT,
        "trajectory_id": "synthetic_only",
        "task_id": "synthetic_only",
        "scenario_id": "synthetic_only",
        "split": "train",
        "actor_role": "teacher",
        "start_kind": "original_initial",
        "messages": messages,
        "turns": [
            {
                "assistant_index": 2,
                "history_hash": digest(messages[:2]),
                "tools": tools[:1],
            },
            {
                "assistant_index": 4,
                "history_hash": digest(messages[:4]),
                "tools": tools,
            },
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    counts = Counter()
    text_categories = Counter()
    tokenizer = load_tokenizer()
    example = fixture()
    encoded = assistant_examples(example, tokenizer)
    assert len(encoded) == 2
    for row in encoded:
        start = row["target_start"]
        assert row["labels"][:start] == [-100] * start
        assert row["labels"][start:] == row["input_ids"][start:]
        target_text = tokenizer.decode(
            row["input_ids"][start:], skip_special_tokens=False
        )
        assert "SYNTHETIC_TOOL_CONTEXT" not in target_text
        assert "NEVER_TRAIN_THIS_TOOL_TEXT" not in target_text
        assert "SYNTHETIC_USER_CONTEXT" not in target_text
        assert "SYNTHETIC_SYSTEM_CONTEXT" not in target_text
        message = parse_completion(target_text, "synthetic_roundtrip")
        original = example["messages"][row["assistant_index"]]["tool_calls"][0][
            "function"
        ]
        parsed = message["tool_calls"][0]["function"]
        assert parsed["name"] == original["name"]
        assert json.loads(parsed["arguments"]) == json.loads(original["arguments"])
    padded = pad_examples(encoded, tokenizer.pad_token_id)
    for row in padded:
        assert all(
            label == -100
            for label, attention in zip(row["labels"], row["attention_mask"])
            if attention == 0
        )
    first_input = tokenizer.decode(encoded[0]["input_ids"])
    assert "demo__finish" not in first_input
    assert "demo__finish" in tokenizer.decode(encoded[1]["input_ids"])
    # A native batch stays one Assistant training target, with both results masked.
    import copy
    import re

    batch = copy.deepcopy(example)
    second_call = copy.deepcopy(batch["messages"][2]["tool_calls"][0])
    second_call["id"] = "synthetic_batch_extra"
    second_call["function"]["arguments"] = json.dumps(
        {"text": "second independent call"}
    )
    batch["messages"][2]["tool_calls"].append(second_call)
    batch["messages"].insert(
        4,
        {
            "role": "tool",
            "name": "demo__lookup",
            "tool_call_id": "synthetic_batch_extra",
            "content": "SECOND_TOOL_MASK_ONLY",
        },
    )
    batch["turns"][1]["assistant_index"] = 5
    batch["turns"][1]["history_hash"] = digest(batch["messages"][:5])
    batch_encoded = assistant_examples(batch, tokenizer)
    text = tokenizer.decode(
        batch_encoded[0]["input_ids"][batch_encoded[0]["target_start"] :]
    )
    parsed_calls = [
        json.loads(value)
        for value in re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.S)
    ]
    assert len(parsed_calls) == 2
    for parsed, native in zip(parsed_calls, batch["messages"][2]["tool_calls"]):
        assert parsed["name"] == native["function"]["name"]
        assert parsed["arguments"] == json.loads(native["function"]["arguments"])
    for row in batch_encoded:
        assert row["labels"][: row["target_start"]] == [-100] * row["target_start"]
        assert "SECOND_TOOL_MASK_ONLY" not in tokenizer.decode(
            row["input_ids"][row["target_start"] :]
        )
    for path in sorted(Path(args.historical_run).glob("*/trajectory.json")):
        trajectory = read_json(path)
        for event in trajectory["events"]:
            counts["model_turns"] += 1
            text = event["generation"]["text"]
            if "format_error" in event:
                counts["format_errors"] += 1
                category = (
                    "plain_text_no_tool_tags"
                    if "<tool_call>" not in text and "</tool_call>" not in text
                    else "tagged_invalid"
                )
                text_categories[category] += 1
                if "access token" in text.lower():
                    counts["no_call_text_mentions_access_token"] += 1
            if "validation_error" in event:
                counts["schema_errors"] += 1
            if "Execution failed" in event.get("tool_result", ""):
                counts["environment_execution_errors"] += 1
    write_json(
        args.output,
        {
            "model": MODEL,
            "revision": REVISION,
            "chat_template_sha256": hashlib.sha256(
                tokenizer.chat_template.encode()
            ).hexdigest(),
            "historical_run": args.historical_run,
            "historical_counts": dict(counts),
            "format_categories": dict(text_categories),
            "tokenizer_check": "PASS",
            "synthetic_examples": len(encoded),
            "synthetic_supervised_tokens": sum(
                sum(row["loss_mask"]) for row in encoded
            ),
            "prefix_token_alignment": True,
            "future_schema_excluded": True,
            "system_user_tool_padding_masked": True,
            "structured_argument_roundtrip": True,
            "native_batch_roundtrip_and_mask": True,
            "batch_synthetic_examples": len(batch_encoded),
            "teacher_demonstrations_generated": 0,
            "model_weights_loaded": False,
            "training_started": False,
            "scope": "real tokenizer with synthetic messages; historical Student outputs are diagnosis only",
        },
    )
    print(args.output)


if __name__ == "__main__":
    main()
