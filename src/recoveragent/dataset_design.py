"""Deterministic whole-trajectory deduplication, family split, and loss weights."""

from collections import defaultdict
import copy
import difflib
import json
import random

from .io import digest


def exact_key(sample):
    messages = copy.deepcopy(sample["messages"])
    identities = {}
    for m in messages:
        for c in m.get("tool_calls", []):
            identities[c["id"]] = str(len(identities))
            c["id"] = identities[c["id"]]
        if "tool_call_id" in m:
            m["tool_call_id"] = identities[m["tool_call_id"]]
    return digest(
        {
            "task_id": sample["task_id"],
            "messages": messages,
            "schemas": [t["tools"] for t in sample["turns"]],
        }
    )


def behavior(sample):
    """Only fingerprints leave this function; credentials ignored for similarity."""
    result = []
    for m in sample["messages"]:
        for c in m.get("tool_calls", []):
            f = c["function"]
            if f["name"].startswith("api_docs__"):
                continue
            args = json.loads(f["arguments"])
            args = {
                k: (
                    "<credential>"
                    if k in {"access_token", "password", "verification_code"}
                    else v
                )
                for k, v in args.items()
            }
            result.append(digest([f["name"], args]))
    return result


def design(samples, quality_rows, *, max_per_task=2, similarity_threshold=0.9):
    quality = {r["trajectory_id"]: r for r in quality_rows}
    if len(quality) != len(quality_rows) or {
        s["trajectory_id"] for s in samples
    } != set(quality):
        raise ValueError("Every source requires exactly one tokenizer audit")
    selected, deferred, duplicates, reserve, pairs = [], [], [], [], []
    groups, seen = defaultdict(list), {}
    for sample in sorted(samples, key=lambda s: s["trajectory_id"]):
        identity = sample["trajectory_id"]
        q = quality[identity]
        if (
            q["task_id"] != sample["task_id"]
            or q["scenario_id"] != sample["scenario_id"]
        ):
            raise ValueError("Quality identity mismatch")
        if not q["ready_for_context_limit"]:
            deferred.append(
                {
                    "trajectory_id": identity,
                    "reason": "context_or_target_limit",
                    "max_sequence_tokens": q["max_sequence_tokens"],
                    "max_target_tokens": q["max_target_tokens"],
                }
            )
            continue
        key = exact_key(sample)
        if key in seen:
            duplicates.append(
                {"trajectory_id": identity, "duplicate_of": seen[key], "exact_key": key}
            )
            continue
        seen[key] = identity
        groups[sample["task_id"]].append(sample)
    clusters = {}
    for task, members in sorted(groups.items()):
        traces = {s["trajectory_id"]: behavior(s) for s in members}
        parent = {k: k for k in traces}

        def find(k):
            while parent[k] != k:
                k = parent[k]
            return k

        for i, left in enumerate(members):
            for right in members[i + 1 :]:
                a, b = left["trajectory_id"], right["trajectory_id"]
                score = difflib.SequenceMatcher(
                    None, traces[a], traces[b], autojunk=False
                ).ratio()
                if score >= similarity_threshold:
                    parent[find(b)] = find(a)
                    pairs.append(
                        {"task_id": task, "left": a, "right": b, "similarity": score}
                    )

        def rank(s):
            version = s.get("actor_prompt_variant", "legacy_v1")
            v = {
                "execution_v5": 5,
                "execution_v4": 4,
                "execution_v3": 3,
                "budget_batched_v2": 2,
            }.get(version, 1)
            return (
                -v,
                s.get("tool_mode", "dynamic_discovery") != "dynamic_discovery",
                quality[s["trajectory_id"]]["stats"]["input_tokens"],
                s["trajectory_id"],
            )

        ordered = sorted(members, key=rank)
        # Prefer distinct behavior clusters, then fill up to the explicit task cap.
        chosen, used = [], set()
        for s in ordered:
            cluster = find(s["trajectory_id"])
            if cluster not in used and len(chosen) < max_per_task:
                chosen.append(s)
                used.add(cluster)
        for s in ordered:
            if s not in chosen and len(chosen) < max_per_task:
                chosen.append(s)
        selected.extend(chosen)
        for s in ordered:
            identity = s["trajectory_id"]
            clusters[identity] = find(identity)
            if s not in chosen:
                reserve.append(
                    {
                        "trajectory_id": identity,
                        "reason": "per_task_cap",
                        "task_id": task,
                    }
                )
    return {
        "selected_ids": [
            s["trajectory_id"]
            for s in sorted(selected, key=lambda s: s["trajectory_id"])
        ],
        "deferred": deferred,
        "duplicates": duplicates,
        "reserve": reserve,
        "similar_pairs": pairs,
        "clusters": clusters,
        "max_per_task": max_per_task,
        "similarity_threshold": similarity_threshold,
    }


def family_split(task_scenarios, seed, validation_families=6):
    families = sorted(set(task_scenarios.values()))
    if not 0 < validation_families < len(families):
        raise ValueError("Need disjoint nonempty train and validation families")
    random.Random(seed).shuffle(families)
    validation = set(families[:validation_families])
    return {s: "validation" if s in validation else "train" for s in sorted(families)}


def example_weights(samples, quality_rows, clusters):
    """Equal family/task/behavior/trajectory mass, then equal Assistant-turn mass.

    Each row's loss is its mean supervised-token CE; multiply by this weight,
    then mean over rows (not a concatenated token mean). Weights have mean one.
    """
    q = {r["trajectory_id"]: r for r in quality_rows}
    families = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for s in samples:
        families[s["scenario_id"]][s["task_id"]][clusters[s["trajectory_id"]]].append(s)
    n = sum(q[s["trajectory_id"]]["stats"]["examples"] for s in samples)
    weights = {}
    for tasks in families.values():
        for groups in tasks.values():
            for members in groups.values():
                for s in members:
                    identity = s["trajectory_id"]
                    weights[identity] = n / (
                        len(families)
                        * len(tasks)
                        * len(groups)
                        * len(members)
                        * q[identity]["stats"]["examples"]
                    )
    if abs(sum(weights[k] * q[k]["stats"]["examples"] for k in weights) - n) > 1e-6:
        raise ValueError("Weight normalization mismatch")
    return weights
