"""Freeze complete Assistant rounds from three immutable, accepted data pools."""

from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
import random

from .demonstrations import sha256
from .v2_tasks import context_for


def rows(path):
    with gzip.open(path, "rb") as stream:
        for index, line in enumerate(stream):
            yield index, hashlib.sha256(line).hexdigest(), json.loads(line)


def inspect_pool(path, expected):
    if sha256(path) != expected["sha256"]:
        raise ValueError("Accepted source file changed")
    entries = []
    for index, checksum, row in rows(path):
        context = context_for(row["task_id"], training=True)
        ids, labels, mask = row["input_ids"], row["labels"], row["loss_mask"]
        start = row["target_start"]
        if (
            not 0 < start < len(ids) <= 65536
            or len(ids) != len(labels)
            or len(ids) != len(mask)
            or labels[:start] != [-100] * start
            or mask != [0] * start + [1] * (len(ids) - start)
            or labels[start:] != ids[start:]
        ):
            raise ValueError("Changed complete Assistant target or prefix mask")
        entries.append(
            {
                "source_row": index,
                "source_row_sha256": checksum,
                "task_id": row["task_id"],
                "scenario_id": context["scenario_id"],
                "trajectory_id": row["trajectory_id"],
                "assistant_index": row["assistant_index"],
                "history_hash": row["history_hash"],
                "tools_hash": row["tools_hash"],
                "sequence_tokens": len(ids),
                "target_start": start,
                "supervised_tokens": sum(mask),
            }
        )
    if (
        len(entries) != expected["examples"]
        or len({r["task_id"] for r in entries}) != expected["tasks"]
        or sum(r["supervised_tokens"] for r in entries) != expected["supervised_tokens"]
    ):
        raise ValueError("Accepted source pool coverage changed")
    return entries


def balanced_select(entries, count, seed):
    """Cover scenes, then unseen tasks, then balance remaining task presentations."""
    if count > len(entries):
        raise ValueError("Not enough distinct complete rounds")
    rng = random.Random(seed)
    pools = defaultdict(list)
    shuffled = list(entries)
    rng.shuffle(shuffled)
    for row in shuffled:
        pools[row["task_id"]].append(row)
    tie = {task: rng.random() for task in sorted(pools)}
    tasks, scenes, chosen = Counter(), Counter(), []
    for _ in range(count):
        task = min(
            (t for t, rs in pools.items() if rs),
            key=lambda t: (
                scenes[pools[t][-1]["scenario_id"]] > 0,
                tasks[t] > 0,
                scenes[pools[t][-1]["scenario_id"]],
                tasks[t],
                tie[t],
            ),
        )
        row = pools[task].pop()
        tasks[task] += 1
        scenes[row["scenario_id"]] += 1
        chosen.append(row)
    return chosen


def weight_plan(groups, alphas):
    if set(groups) != set(alphas) or not math.isclose(sum(alphas.values()), 1):
        raise ValueError("Source objectives must sum to one")
    total = sum(map(len, groups.values()))
    result, stats = [], {}
    for source, entries in groups.items():
        tokens = sum(r["supervised_tokens"] for r in entries)
        if not entries or tokens <= 0 or alphas[source] <= 0:
            raise ValueError("Empty or invalid source objective")
        weighted = [
            {
                **r,
                "source": source,
                "example_weight": alphas[source]
                * total
                * r["supervised_tokens"]
                / tokens,
            }
            for r in entries
        ]
        result.extend(weighted)
        stats[source] = {
            "task_ids": sorted({r["task_id"] for r in entries}),
            "tasks": len({r["task_id"] for r in entries}),
            "scenarios": len({r["scenario_id"] for r in entries}),
            "examples": len(entries),
            "supervised_tokens": tokens,
            "weight_sum": sum(r["example_weight"] for r in weighted),
            "objective_fraction": alphas[source],
        }
    return result, stats


def freeze(root, cfg):
    selected = {}
    for source, expected in cfg["data_pools"].items():
        entries = inspect_pool(root / expected["file"], expected)
        selected[source] = balanced_select(
            entries, cfg["presentations"][source], cfg["training_seed"]
        )
        if source != "old_replay" and (
            {r["task_id"] for r in entries} != {r["task_id"] for r in selected[source]}
        ):
            raise ValueError("Fresh/recovery task omitted")
    plan, stats = weight_plan(selected, cfg["recovery_sft"]["source_objectives"])
    return {
        "seed": cfg["training_seed"],
        "rows": plan,
        "groups": stats,
        "examples": len(plan),
        "historical_text_changed": False,
        "truncated": False,
        "selection": "scene_then_task_balanced_without_replacement",
    }


def materialize(root, cfg, plan):
    result = []
    for source, pool in cfg["data_pools"].items():
        if sha256(root / pool["file"]) != pool["sha256"]:
            raise ValueError("Frozen source changed")
        selected = {r["source_row"]: r for r in plan["rows"] if r["source"] == source}
        for index, checksum, row in rows(root / pool["file"]):
            if index in selected:
                entry = selected.pop(index)
                if checksum != entry["source_row_sha256"]:
                    raise ValueError("Frozen round content changed")
                result.append(
                    {
                        **row,
                        "teacher_data_kind": source,
                        "example_weight": entry["example_weight"],
                        "selection_provenance": entry,
                    }
                )
        if selected:
            raise ValueError("Missing frozen rounds")
    if len(result) != plan["examples"]:
        raise ValueError("Frozen presentation count changed")
    return result
