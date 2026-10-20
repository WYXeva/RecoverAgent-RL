"""Qwen3-4B tokenization/masks only. This module never loads model weights or trains."""

import argparse
import hashlib
import json
from pathlib import Path

from .demonstrations import validate_demo, sha256
from .io import digest, read_json, write_json

MODEL = "Qwen/Qwen3-4B"
REVISION = "1cfa9a7208912126459214e8b04321603b3df60c"
TEMPLATE_SHA256 = "a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8"


def load_tokenizer():
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        MODEL, revision=REVISION, local_files_only=True
    )
    if hashlib.sha256(tokenizer.chat_template.encode()).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("Pinned Qwen3-4B tool chat template changed")
    return tokenizer


def assistant_examples(record, tokenizer, max_length=32768):
    """One target Assistant turn per example, with its actual then-visible tools.

    Rendering the whole dialogue with its final schema would expose future APIs.
    Re-rendering past Qwen turns can also remove empty thinking blocks. We instead
    independently verify the exact generation-prefix relation at each turn.
    """
    validate_demo(record)
    examples = []
    for turn in record["turns"]:
        index = turn["assistant_index"]
        history = record["messages"][:index]
        message = record["messages"][index]
        kwargs = {"tools": turn["tools"], "enable_thinking": False, "tokenize": False}
        prefix = tokenizer.apply_chat_template(
            history, add_generation_prompt=True, **kwargs
        )
        full = tokenizer.apply_chat_template(
            history + [message], add_generation_prompt=False, **kwargs
        )
        if not full.startswith(prefix):
            raise ValueError("Template changed the visible generation prefix")
        prefix_ids = tokenizer(prefix, add_special_tokens=False)["input_ids"]
        full_ids = tokenizer(full, add_special_tokens=False)["input_ids"]
        if full_ids[: len(prefix_ids)] != prefix_ids:
            raise ValueError("Token boundary changed at the Assistant target")
        if len(full_ids) > max_length:
            raise ValueError("Context too long; refusing silent truncation")
        if len(full_ids) == len(prefix_ids):
            raise ValueError("Empty Assistant supervision")
        labels = [-100] * len(prefix_ids) + full_ids[len(prefix_ids) :]
        examples.append(
            {
                "trajectory_id": record["trajectory_id"],
                "task_id": record["task_id"],
                "scenario_id": record["scenario_id"],
                "assistant_index": index,
                "input_ids": full_ids,
                "labels": labels,
                "attention_mask": [1] * len(full_ids),
                "loss_mask": [int(label != -100) for label in labels],
                "target_start": len(prefix_ids),
                "history_hash": turn["history_hash"],
                "tools_hash": digest(turn["tools"]),
            }
        )
    return examples


def pad_examples(examples, pad_token_id):
    """Right padding with zero attention and -100 labels; no sequence packing."""
    size = max(len(x["input_ids"]) for x in examples)
    result = []
    for example in examples:
        length = size - len(example["input_ids"])
        result.append(
            {
                **example,
                "input_ids": example["input_ids"] + [pad_token_id] * length,
                "labels": example["labels"] + [-100] * length,
                "attention_mask": example["attention_mask"] + [0] * length,
                "loss_mask": example["loss_mask"] + [0] * length,
            }
        )
    return result


def prepare(dataset, output, expected_manifest_sha256, max_length=32768):
    # The AppWorld environment must already have performed full verify_dataset.
    # The verification receipt binds that exact manifest, split and sample order.
    dataset, output = Path(dataset), Path(output)
    if sha256(dataset / "manifest.json") != expected_manifest_sha256:
        raise ValueError("Dataset differs from the verified transfer")
    manifest = read_json(dataset / "manifest.json")
    if manifest.get("target_student") != {
        "model": MODEL,
        "revision": REVISION,
        "thinking": False,
    }:
        raise ValueError("Dataset must target the pinned non-thinking Qwen3-4B")
    from .io import files_manifest

    files = files_manifest(dataset)
    for name in ("manifest.json", "manifest.sha256"):
        files.pop(name)
    if files != manifest["files"]:
        raise ValueError("Transferred data changed after verification")
    receipt = read_json(dataset.parent / (dataset.name + ".verified.json"))
    if (
        receipt.get("manifest_sha256") != expected_manifest_sha256
        or receipt.get("dataset_id") != manifest["dataset_id"]
        or receipt.get("sample_order_hash") != manifest["sample_order_hash"]
        or receipt.get("official_train_membership_checked") is not True
    ):
        raise ValueError("AppWorld dataset verification receipt required")
    samples = [
        json.loads(line)
        for line in (dataset / "demonstrations.jsonl").read_text().splitlines()
    ]
    if not samples:
        raise ValueError("No verified Teacher demonstrations; cannot prepare SFT data")
    if [s["trajectory_id"] for s in samples] != manifest["sample_order"]:
        raise ValueError("Sample order changed")
    tokenizer = load_tokenizer()
    output.mkdir(parents=True, exist_ok=False)
    counts = {"examples": 0, "input_tokens": 0, "supervised_tokens": 0}
    with (output / "assistant_examples.jsonl").open("x") as stream:
        for sample in samples:
            for example in assistant_examples(sample, tokenizer, max_length):
                stream.write(json.dumps(example, ensure_ascii=False) + "\n")
                counts["examples"] += 1
                counts["input_tokens"] += len(example["input_ids"])
                counts["supervised_tokens"] += sum(example["loss_mask"])
    report = {
        "model": MODEL,
        "revision": REVISION,
        "thinking": False,
        "chat_template_sha256": hashlib.sha256(
            tokenizer.chat_template.encode()
        ).hexdigest(),
        "source_dataset_id": manifest["dataset_id"],
        "source_manifest_sha256": expected_manifest_sha256,
        "counts": counts,
        "strategy": "one_assistant_turn_with_actual_visible_schema",
        "examples_sha256": sha256(output / "assistant_examples.jsonl"),
        "training_started": False,
        "weights_loaded": False,
        "split_rule": "keep all turns of each task/scenario together; no random suffix split",
    }
    write_json(output / "preparation.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--max-length", type=int, default=32768)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                args.dataset,
                args.output,
                args.expected_manifest_sha256,
                args.max_length,
            )
        )
    )


if __name__ == "__main__":
    main()
