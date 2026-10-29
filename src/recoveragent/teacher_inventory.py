"""Offline success inventory and lossless JSON-tool rendering experiments.

Original Teacher evidence is always validated unchanged. A compact view is a
derived training input, never a replacement provider receipt or a new success.
This module does not execute tools, call a model, or resume a collector.
"""

import argparse
import copy
import gzip
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
from .demonstrations import as_demo, sha256, validate_demo
from .io import canonical, digest, files_manifest, read_json, write_json
from .parallel_teacher import read_review
from .student import parse_batch

FORMAT = "teacher-success-inventory-v1"
COMPACT = "json-tool-whitespace-only-v1"


def record_line(record):
    # Qwen renders nested schema dictionaries in insertion order. Canonical
    # sort_keys serialization is suitable for hashes, not token-exact replay.
    return json.dumps(
        record, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def compact_json(text):
    """Remove only JSON whitespace outside strings; preserve every other byte.

    Do not parse/re-serialize numbers or objects: that can round large numbers,
    change escape spelling, or discard duplicate keys. Non-JSON stays verbatim.
    """
    if not text.lstrip().startswith(("{", "[")):
        return text

    def reject_constant(_):
        raise ValueError("Non-JSON constant")

    try:
        json.loads(text, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        return text
    result, in_string, escaped = [], False, False
    for char in text:
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            result.append(char)
            in_string = True
        elif char not in " \t\r\n":
            result.append(char)
    return "".join(result)


def compact_messages(messages):
    result = copy.deepcopy(messages)
    for message in result:
        if message["role"] == "tool":
            message["content"] = compact_json(message["content"])
    return result


class CompactTokenizer:
    """Reuse the existing encoder/masks after validating ORIGINAL evidence."""

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def apply_chat_template(self, messages, **kwargs):
        return self.tokenizer.apply_chat_template(compact_messages(messages), **kwargs)

    def __call__(self, *args, **kwargs):
        return self.tokenizer(*args, **kwargs)


def encode(record, tokenizer, compact=False):
    # Measure full sequences first. Never truncate or discard later turns.
    renderer = CompactTokenizer(tokenizer) if compact else tokenizer
    examples = assistant_examples(record, renderer, max_length=2**31 - 1)
    turns = [t for t in record["turns"] if t.get("supervise") is not False]
    if not examples or len(examples) != len(turns):
        raise ValueError("Missing Assistant targets")
    for example, turn in zip(examples, turns, strict=True):
        start, ids = example["target_start"], example["input_ids"]
        if (
            example["labels"] != [-100] * start + ids[start:]
            or example["loss_mask"] != [0] * start + [1] * (len(ids) - start)
            or example["attention_mask"] != [1] * len(ids)
            or example["tools_hash"] != digest(turn["tools"])
        ):
            raise ValueError("Assistant-only mask differs")
        index = example["assistant_index"]
        expected = record["messages"][index]
        parsed = parse_batch(tokenizer.decode(ids[start:]), "inventory", "stop")

        def calls(message):
            return [
                (c["function"]["name"], json.loads(c["function"]["arguments"]))
                for c in message.get("tool_calls", [])
            ]

        if (
            calls(parsed) != calls(expected)
            or parsed["content"].strip() != expected["content"].strip()
        ):
            raise ValueError("Student parser changes original Teacher target")
        history = record["messages"][:index]
        example.update(
            representation=COMPACT if compact else "original-clean-v1",
            source_history_hash=example["history_hash"],
            history_hash=digest(compact_messages(history) if compact else history),
            source_demo_sha256=digest(record),
            assistant_message_sha256=digest(expected),
        )
    for example in pad_examples([examples[0], examples[-1]], tokenizer.pad_token_id):
        if any(
            label != -100 or mask != 0
            for label, mask, attention in zip(
                example["labels"],
                example["loss_mask"],
                example["attention_mask"],
                strict=True,
            )
            if attention == 0
        ):
            raise ValueError("Padding leaked into loss")
    return examples


def measures(examples):
    return {
        "assistant_samples": len(examples),
        "supervised_tokens": sum(sum(e["loss_mask"]) for e in examples),
        "masked_tokens": sum(e["target_start"] for e in examples),
        "total_training_sequence_tokens": sum(len(e["input_ids"]) for e in examples),
        "longest_sequence": max(len(e["input_ids"]) for e in examples),
        "longest_target": max(
            len(e["input_ids"]) - e["target_start"] for e in examples
        ),
        "over_context_samples": sum(len(e["input_ids"]) > 32768 for e in examples),
        "over_target_samples": sum(
            len(e["input_ids"]) - e["target_start"] > 4096 for e in examples
        ),
    }


def fits(stats):
    return stats["over_context_samples"] == stats["over_target_samples"] == 0


def load_source(item):
    home, source = Path(item["review"]), Path(item["source"])
    if sha256(home / "review.json") != item["review_sha256"]:
        raise ValueError("Frozen success review changed")
    review = read_review(home)
    if (
        review["task_id"] != item["task_id"]
        or review["directory"] != str(source)
        or review["official_success"] is not True
        or review["original_status"] not in {"SUCCEEDED", "DEFERRED"}
    ):
        raise ValueError("Not a sealed successful source")
    if sha256(source / "evidence_manifest.json") != item["source_manifest_sha256"]:
        raise ValueError("Frozen source manifest changed")
    expected = read_json(source / "evidence_manifest.json")["files"]
    actual = files_manifest(source)
    actual.pop("evidence_manifest.json")
    if actual != expected:
        raise ValueError("Original source files changed")
    result = read_json(source / "result.json")
    receipt = read_json(source / "replay/receipt.json")
    verdict = receipt["snapshot_scoring_integrity"]
    if (
        result["official_success"] is not True
        or result["replay_verified"] is not True
        or verdict["status"] != "PASS"
        or len(verdict["checks"]) != 10
        or not all(v is True for v in verdict["checks"].values())
    ):
        raise ValueError("Successful terminal restoration not verified")
    record = as_demo(read_json(source / "trajectory.json"))
    validate_demo(record)
    if (
        record["task_id"] != item["task_id"]
        or record["scenario_id"] != item["scenario_id"]
        or record["split"] != item["official_split"]
        or review["eligible_for_sft"] != item["original_eligible"]
        or any("reasoning_content" in m for m in record["messages"])
    ):
        raise ValueError("Source identity, eligibility or clean message mismatch")
    return record, result


def validate_plan(plan):
    body = {k: v for k, v in plan.items() if k != "manifest_sha256"}
    if digest(body) != plan["manifest_sha256"] or plan["format"] != FORMAT:
        raise ValueError("Frozen inventory plan changed")
    tasks = [r["task_id"] for r in plan["sources"]]
    if tasks != sorted(set(tasks)):
        raise ValueError("Duplicate or unordered successful tasks")
    for source in plan["anchors"]:
        if sha256(source["path"]) != source["sha256"]:
            raise ValueError("Original inventory anchor changed")


def build(config, output, tokenizer=None):
    plan = read_json(config)
    validate_plan(plan)
    tokenizer = tokenizer or load_tokenizer()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    rows, seen = [], set()
    names = (
        "successes.jsonl",
        "native_candidates.jsonl",
        "compact_candidates.jsonl",
        "deferred.jsonl",
    )
    from contextlib import ExitStack

    with ExitStack() as stack:
        streams = {
            name: stack.enter_context((output / name).open("x")) for name in names
        }
        raw = stack.enter_context((output / "assistant_examples.jsonl.gz").open("xb"))
        encoded = stack.enter_context(
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0)
        )
        for item in plan["sources"]:
            record, result = load_source(item)
            if record["trajectory_id"] in seen:
                raise ValueError("Duplicate trajectory source")
            seen.add(record["trajectory_id"])
            native = encode(record, tokenizer)
            stats = measures(native)
            if fits(stats) != item["original_eligible"]:
                raise ValueError("Re-tokenization differs from sealed qualification")
            if fits(stats) and any(
                stats[k] != result[k]
                for k in ("assistant_samples", "supervised_tokens", "longest_sequence")
            ):
                raise ValueError("Original encoding counts changed")
            streams["successes.jsonl"].write(record_line(record) + "\n")
            row = dict(item, native=stats, trajectory_id=record["trajectory_id"])
            row["source_demo_sha256"] = digest(record)
            selected = native
            if fits(stats):
                row["disposition"] = "NATIVE"
                streams["native_candidates.jsonl"].write(record_line(record) + "\n")
            else:
                compact = encode(record, tokenizer, compact=True)
                for a, b in zip(native, compact, strict=True):
                    if (
                        a["input_ids"][a["target_start"] :]
                        != b["input_ids"][b["target_start"] :]
                    ):
                        raise ValueError("Compaction changed Teacher target tokens")
                row["compact"] = measures(compact)
                row["disposition"] = (
                    "COMPACT_DERIVED" if fits(row["compact"]) else "DEFERRED"
                )
                changes = []
                for index, (a, b) in enumerate(
                    zip(
                        record["messages"],
                        compact_messages(record["messages"]),
                        strict=True,
                    )
                ):
                    if a != b:
                        changes.append(
                            {
                                "message_index": index,
                                "tool_call_id": a["tool_call_id"],
                                "source_content_sha256": digest(a["content"]),
                                "rendered_content_sha256": digest(b["content"]),
                                "removed_characters": len(a["content"])
                                - len(b["content"]),
                            }
                        )
                row["changes"] = changes
                derived = {
                    "kind": COMPACT,
                    "source_demo_sha256": digest(record),
                    "trajectory_id": record["trajectory_id"],
                    "task_id": record["task_id"],
                    "changes": changes,
                    "original_reward_unchanged": True,
                    "new_environment_execution": False,
                }
                if fits(row["compact"]):
                    streams["compact_candidates.jsonl"].write(canonical(derived) + "\n")
                    selected = compact
                else:
                    streams["deferred.jsonl"].write(
                        canonical(
                            dict(
                                derived, reason="complete_sequence_still_exceeds_budget"
                            )
                        )
                        + "\n"
                    )
                    selected = []
            for example in selected:
                encoded.write((canonical(example) + "\n").encode())
            rows.append(row)
            print(
                item["task_id"],
                row["disposition"],
                stats["longest_sequence"],
                row.get("compact", {}).get("longest_sequence"),
                flush=True,
            )
    write_json(output / "inventory.json", rows)
    accepted = [r for r in rows if r["disposition"] != "DEFERRED"]
    selected_stats = [
        r["compact"] if r["disposition"] == "COMPACT_DERIVED" else r["native"]
        for r in accepted
    ]
    summary = {
        "official_successes": len(rows),
        "unique_tasks": len({r["task_id"] for r in rows}),
        "unique_scenarios": len({r["scenario_id"] for r in rows}),
        "dispositions": dict(Counter(r["disposition"] for r in rows)),
        "accepted_tokenized_candidates": len(accepted),
        "assistant_samples": sum(r["assistant_samples"] for r in selected_stats),
        "supervised_tokens": sum(r["supervised_tokens"] for r in selected_stats),
        "masked_tokens": sum(r["masked_tokens"] for r in selected_stats),
        "longest_accepted_sequence": max(r["longest_sequence"] for r in selected_stats),
        "original_splits": dict(Counter(r["official_split"] for r in rows)),
        "removed_tool_calls": 0,
        "removed_json_fields": 0,
        "new_model_requests": 0,
    }
    manifest = {
        "format": FORMAT,
        "source_plan_sha256": sha256(config),
        "student": {
            "model": MODEL,
            "revision": REVISION,
            "thinking": False,
            "template_sha256": TEMPLATE_SHA256,
        },
        "compact_policy": COMPACT,
        "summary": summary,
        "source_files": {
            name: sha256(Path(__file__).parent / name)
            for name in (
                "teacher_inventory.py",
                "base_sft.py",
                "demonstrations.py",
                "student.py",
            )
        },
        "files": files_manifest(output),
        "training_started": False,
        "runtime_compaction_enabled": False,
        "scope": "Private Full732 custom A research; sealed successes only. Derived formatting is not an original Teacher request. No old95 or unfinished attempt included.",
    }
    write_json(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(
        sha256(output / "manifest.json") + "  manifest.json\n"
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(canonical(build(args.config, args.output)["summary"]))


if __name__ == "__main__":
    main()
