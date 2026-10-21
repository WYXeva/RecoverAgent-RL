"""Audit verified Teacher data with real Qwen3-4B tokens; never train or truncate."""

import argparse
import json
from collections import Counter
from pathlib import Path

from .base_sft import (
    MODEL,
    REVISION,
    TEMPLATE_SHA256,
    assistant_examples,
    load_tokenizer,
    pad_examples,
)
from .demonstrations import sha256
from .io import digest, files_manifest, read_json, write_json
from .student import parse_batch


def checked_samples(dataset, expected_hash):
    dataset = Path(dataset)
    if sha256(dataset / "manifest.json") != expected_hash:
        raise ValueError("Source manifest hash mismatch")
    manifest = read_json(dataset / "manifest.json")
    receipt = read_json(dataset.parent / (dataset.name + ".verified.json"))
    if (
        receipt.get("manifest_sha256") != expected_hash
        or receipt.get("dataset_id") != manifest["dataset_id"]
        or receipt.get("sample_order_hash") != manifest["sample_order_hash"]
        or receipt.get("official_train_membership_checked") is not True
        or manifest["target_student"]
        != {"model": MODEL, "revision": REVISION, "thinking": False}
    ):
        raise ValueError("Verified pinned Student dataset receipt required")
    files = files_manifest(dataset)
    for name in ("manifest.json", "manifest.sha256"):
        files.pop(name)
    if files != manifest["files"]:
        raise ValueError("Source data changed since verification")
    samples = [
        json.loads(line)
        for line in (dataset / "demonstrations.jsonl").read_text().splitlines()
    ]
    if [s["trajectory_id"] for s in samples] != manifest["sample_order"] or len(
        samples
    ) != manifest["counts"]["accepted"]:
        raise ValueError("Source order/count mismatch")
    return manifest, samples


def audit_samples(samples, tokenizer, output, max_length):
    """All samples are measured; only whole trajectories within max_length are ready."""
    if type(max_length) is not int or max_length <= 0:
        raise ValueError("Positive context limit required")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    records, total, ready, prior_schema_changes = [], Counter(), Counter(), 0
    with (output / "ready_assistant_examples.jsonl").open("x") as stream:
        for sample in samples:
            # Diagnostic ceiling only; no sequence is clipped. Normal deployment
            # length is applied below and whole overlength trajectories are deferred.
            examples = assistant_examples(sample, tokenizer, max_length=2_000_000)
            target_turns = [
                t for t in sample["turns"] if t.get("supervise") is not False
            ]
            if not examples or len(examples) != len(target_turns):
                raise ValueError("Missing Assistant targets")
            stats, previous, previous_schema = Counter(), None, None
            for row, turn in zip(examples, target_turns):
                start, ids = row["target_start"], row["input_ids"]
                if (
                    row["labels"][:start] != [-100] * start
                    or row["labels"][start:] != ids[start:]
                    or row["loss_mask"] != [0] * start + [1] * (len(ids) - start)
                    or row["tools_hash"] != digest(turn["tools"])
                ):
                    raise ValueError("Assistant-only mask or actual schema mismatch")
                parsed = parse_batch(
                    tokenizer.decode(ids[start:], skip_special_tokens=False),
                    "roundtrip",
                    "stop",
                )
                target = sample["messages"][turn["assistant_index"]]
                expected = [
                    {
                        "name": c["function"]["name"],
                        "arguments": json.loads(c["function"]["arguments"]),
                    }
                    for c in target.get("tool_calls", [])
                ]
                actual = [
                    {
                        "name": c["function"]["name"],
                        "arguments": json.loads(c["function"]["arguments"]),
                    }
                    for c in parsed.get("tool_calls", [])
                ]
                if (
                    actual != expected
                    or parsed["content"].strip() != target["content"].strip()
                ):
                    raise ValueError(
                        "Student parser cannot recover the Base-SFT target"
                    )
                stats.update(
                    examples=1,
                    input_tokens=len(ids),
                    context_tokens=start,
                    supervised_tokens=len(ids) - start,
                    roundtrip_calls=len(actual),
                    batch_targets=int(len(actual) > 1),
                    overlength_turns=int(len(ids) > max_length),
                    over_output_limit_turns=int(len(ids) - start > 4096),
                )
                if previous is not None:
                    shared = next(
                        (i for i, (a, b) in enumerate(zip(previous, ids)) if a != b),
                        min(len(previous), len(ids)),
                    )
                    stats["repeated_identical_prefix_tokens"] += shared
                    stats["schema_changes"] += int(previous_schema != row["tools_hash"])
                previous, previous_schema = ids, row["tools_hash"]
            for row in pad_examples(
                [examples[0], examples[-1]], tokenizer.pad_token_id
            ):
                if any(
                    label != -100 or mask != 0
                    for label, mask, attn in zip(
                        row["labels"], row["loss_mask"], row["attention_mask"]
                    )
                    if attn == 0
                ):
                    raise ValueError("Padding leaked into loss")
            fits = not (stats["overlength_turns"] or stats["over_output_limit_turns"])
            stats["format_rejected_context_only_turns"] = len(sample["turns"]) - len(
                target_turns
            )
            if fits:
                for row in examples:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                ready.update(stats)
            total.update(stats)
            prior_schema_changes += stats["schema_changes"]
            records.append(
                {
                    "trajectory_id": sample["trajectory_id"],
                    "task_id": sample["task_id"],
                    "scenario_id": sample["scenario_id"],
                    "tool_mode": sample.get("tool_mode"),
                    "max_sequence_tokens": max(len(r["input_ids"]) for r in examples),
                    "max_target_tokens": max(sum(r["loss_mask"]) for r in examples),
                    "last_turn_sequence_tokens": len(examples[-1]["input_ids"]),
                    "ready_for_context_limit": fits,
                    "stats": dict(stats),
                }
            )
    last_sum = sum(r["last_turn_sequence_tokens"] for r in records)
    qualified = [r for r in records if r["ready_for_context_limit"]]
    return {
        "status": "PASS" if records and len(qualified) == len(records) else "PARTIAL",
        "max_length": max_length,
        "student_max_new_tokens": 4096,
        "raw_clean_demonstrations": len(records),
        "unique_tasks": len({r["task_id"] for r in records}),
        "scenario_coverage": dict(Counter(r["scenario_id"] for r in records)),
        "ready_complete_demonstrations": len(qualified),
        "ready_scenarios": len({r["scenario_id"] for r in qualified}),
        "max_sequence_tokens": max(
            (r["max_sequence_tokens"] for r in records), default=0
        ),
        "max_target_tokens": max((r["max_target_tokens"] for r in records), default=0),
        "all_clean_token_counts": dict(total),
        "ready_token_counts": dict(ready),
        "cumulative_context_fraction": total["context_tokens"] / total["input_tokens"]
        if records
        else None,
        "per_turn_expansion_over_final_sequences": total["input_tokens"] / last_sum
        if last_sum
        else None,
        "repetition_definition": "sum of exactly identical token-prefix lengths between adjacent turn examples; schema rerendering may shorten prefixes",
        "expansion_definition": "sum of all turn input lengths / sum of each trajectory final-turn input length; not a count of unique semantic tokens",
        "actual_schema_changes_checked": prior_schema_changes,
        "prefix_and_assistant_only_and_padding_masks_checked": True,
        "student_parser_roundtrip_checked": True,
        "overlength_policy": "defer entire trajectory exceeding context or 4096-token target limit; no silent truncation or target-only clipping",
        "rows": records,
        "ready_examples_sha256": sha256(output / "ready_assistant_examples.jsonl"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-length", type=int, default=32768)
    args = parser.parse_args()
    if Path(args.output).exists() or Path(args.report).exists():
        parser.error("Use new output and report paths; existing evidence is immutable")
    manifest, samples = checked_samples(args.dataset, args.expected_manifest_sha256)
    result = audit_samples(samples, load_tokenizer(), args.output, args.max_length)
    result.update(
        source_dataset_id=manifest["dataset_id"],
        source_manifest_sha256=args.expected_manifest_sha256,
        model=MODEL,
        revision=REVISION,
        chat_template_sha256=TEMPLATE_SHA256,
        training_started=False,
        weights_loaded=False,
    )
    write_json(Path(args.output) / "quality.json", result)
    write_json(args.report, result)
    print(
        f"{result['ready_complete_demonstrations']} context-ready trajectories; max length {result['max_sequence_tokens']}"
    )


if __name__ == "__main__":
    main()
