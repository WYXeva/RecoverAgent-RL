"""Fresh successful CodeAct demonstrations, exact multi-turn Qwen target masks."""

from collections import Counter
import hashlib
import json
from pathlib import Path

from recoveragent.io import digest, read_json, write_json
from recoveragent.teacher_history import validate_history
from recoveragent.v2_model import load_tokenizer
from recoveragent.v2_tasks import context_for

from .protocol import SYSTEM, TOOLS, identity


def encode(messages, tokenizer):
    validate_history(messages)
    if messages[0] != {"role": "system", "content": SYSTEM}:
        raise ValueError("Not the new CodeAct public input")
    if any("reasoning_content" in m for m in messages):
        raise ValueError("Provider hidden reasoning must not enter SFT")
    rows, repeated, targets, turns = [], 0, 0, []
    for index, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        kwargs = {
            "tools": TOOLS,
            "tokenize": True,
            "return_dict": False,
            "enable_thinking": False,
        }
        prompt = tokenizer.apply_chat_template(
            messages[:index], add_generation_prompt=True, **kwargs
        )
        full = tokenizer.apply_chat_template(
            messages[: index + 1], add_generation_prompt=False, **kwargs
        )
        if full[: len(prompt)] != prompt or len(full) == len(prompt):
            raise ValueError(
                "Assistant target cannot be derived from exact native template prefix"
            )
        repeated += len(full)
        count = len(full) - len(prompt)
        targets += count
        turns.append(
            {
                "message_index": index,
                "target_tokens": count,
                "prompt_tokens": len(prompt),
            }
        )
        previous = rows[-1] if rows else None
        if (
            previous
            and len(previous["input_ids"]) <= len(prompt)
            and full[: len(previous["input_ids"])] == previous["input_ids"]
        ):
            labels = (
                previous["labels"]
                + [-100] * (len(prompt) - len(previous["input_ids"]))
                + full[len(prompt) :]
            )
            previous.update(input_ids=full, labels=labels)
            previous["assistant_indices"].append(index)
        else:
            rows.append(
                {
                    "input_ids": full,
                    "labels": [-100] * len(prompt) + full[len(prompt) :],
                    "assistant_indices": [index],
                }
            )
    if not rows or sum(sum(y != -100 for y in r["labels"]) for r in rows) != targets:
        raise ValueError("Assistant targets missing or duplicated")
    for row in rows:
        row["attention_mask"] = [1] * len(row["input_ids"])
        row["target_tokens"] = sum(y != -100 for y in row["labels"])
    return rows, {
        "assistant_turns": turns,
        "target_tokens": targets,
        "repeated_prefix_input_tokens": repeated,
    }


def export(collection, output, config, *, tasks=None, tokenizer=None):
    collection, output = Path(collection).resolve(), Path(output).resolve()
    binding = read_json(collection / "binding.json")
    if binding["protocol"] != identity() or binding["config_sha256"] != digest(config):
        raise ValueError("Collection provenance/config mismatch")
    tokenizer = tokenizer or load_tokenizer(config["model"], local_files_only=True)
    rows, records, deferred, seen = [], [], [], set()
    for path in sorted(collection.glob("episodes/*/attempt_*/result.json")):
        result = read_json(path)
        task = result["task_id"]
        if (
            not result.get("eligible")
            or task in seen
            or (tasks is not None and task not in tasks)
        ):
            continue
        trajectory = read_json(path.parent / "trajectory.json")
        context_for(task, training=True)
        if (
            result["success"] is not True
            or not result["valid"]
            or trajectory["protocol"] != identity()
            or trajectory["config_sha256"] != binding["config_sha256"]
            or digest(trajectory) != result["trajectory_sha256"]
        ):
            raise ValueError("Unverified fresh success trajectory")
        seen.add(task)
        encoded, stats = encode(trajectory["messages"], tokenizer)
        meta = {
            "task_id": task,
            "scenario_id": trajectory["scenario_id"],
            "attempt": result["attempt"],
            "trajectory": str(path.parent / "trajectory.json"),
            "trajectory_sha256": result["trajectory_sha256"],
            **stats,
            "sequence_lengths": [len(r["input_ids"]) for r in encoded],
        }
        if any(
            len(r["input_ids"]) > config["training"]["max_sequence_tokens"]
            for r in encoded
        ):
            deferred.append(
                meta
                | {
                    "reason": "complete_trajectory_exceeds_sequence_budget",
                    "truncated": False,
                }
            )
            continue
        records.append(meta)
        rows.extend(
            r
            | {
                "task_id": task,
                "trajectory_sha256": meta["trajectory_sha256"],
                "trajectory_target_tokens": stats["target_tokens"],
            }
            for r in encoded
        )
    # Uniform trajectory mean CE, including the rare incompatible-prefix split.
    for row in rows:
        row["example_weight"] = (
            len(rows)
            / len(records)
            * row["target_tokens"]
            / row["trajectory_target_tokens"]
        )
    lengths = sorted(len(r["input_ids"]) for r in rows)
    report = {
        "protocol": identity(),
        "model": config["model"],
        "collection": str(collection),
        "collection_binding_sha256": digest(binding),
        "successful_trajectories": len(seen),
        "trainable_trajectories": len(records),
        "samples": len(rows),
        "deferred_trajectories": len(deferred),
        "input_tokens": sum(lengths),
        "assistant_target_tokens": sum(r["target_tokens"] for r in rows),
        "lengths": lengths,
        "longest": max(lengths, default=0),
        "length_quantiles": {
            str(q): lengths[round((len(lengths) - 1) * q)] if lengths else 0
            for q in (0.5, 0.9, 0.95)
        },
        "repeated_prefix_input_tokens": sum(
            r["repeated_prefix_input_tokens"] for r in records
        ),
        "scenario_counts": dict(Counter(r["scenario_id"] for r in records)),
        "loss": "uniform trajectory mean of all assistant target token CE; microbatch-weighted for prefix splits",
        "truncated": False,
    }
    serialized = "".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows
    )
    report["dataset_sha256"] = hashlib.sha256(serialized.encode()).hexdigest()
    # Re-entrant first-batch validation is read-only when the same export already exists.
    if (output / "manifest.json").exists():
        if (
            read_json(output / "manifest.json") != report
            or hashlib.sha256((output / "train.jsonl").read_bytes()).hexdigest()
            != report["dataset_sha256"]
        ):
            raise ValueError("Export changed; choose a new output directory")
        return report
    output.mkdir(parents=True, exist_ok=False)
    (output / "train.jsonl").write_text(serialized)
    write_json(output / "sources.json", records)
    write_json(output / "deferred.json", deferred)
    write_json(output / "manifest.json", report)
    return report


def load_dataset(directory, config):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    raw = (directory / "train.jsonl").read_bytes()
    if (
        manifest["protocol"] != identity()
        or manifest["model"] != config["model"]
        or hashlib.sha256(raw).hexdigest() != manifest["dataset_sha256"]
    ):
        raise ValueError("Dataset/model/protocol identity mismatch")
    rows = [json.loads(line) for line in raw.splitlines()]
    if not rows:
        raise ValueError("No complete new demonstrations fit the sequence limit")
    for row in rows:
        context_for(row["task_id"], training=True)
        if (
            len(row["input_ids"]) > config["training"]["max_sequence_tokens"]
            or len(row["labels"]) != len(row["input_ids"])
            or row["labels"][0] != -100
            or any(
                label != -100 and label != token
                for label, token in zip(row["labels"], row["input_ids"], strict=True)
            )
            or sum(t != -100 for t in row["labels"]) != row["target_tokens"]
        ):
            raise ValueError("Invalid complete sequence or labels")
    return rows, manifest
