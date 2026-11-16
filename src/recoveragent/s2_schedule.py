"""Deterministic five-round sampling and complete-group feedback, without models."""

from collections import Counter
import copy
import random
from pathlib import Path

from .coevolve_signals import action_patterns, interaction_triplets
from .io import digest, read_json
from .v2_grpo import group_advantages
from .v2_tasks import context_for


def tuples(value):
    return (
        tuple(tuples(x) for x in value) if isinstance(value, (list, tuple)) else value
    )


class TaskQueue:
    def __init__(self, originals, seed, state=None):
        if not originals or len(set(originals)) != len(originals):
            raise ValueError("Original queue must contain unique task IDs")
        self.originals = list(originals)
        self.rng = random.Random(seed)
        self.state = (
            copy.deepcopy(state)
            if state
            else {
                "queue": [],
                "cursor": 0,
                "cycles": 0,
                "round": -1,
                "generated_ever": [],
                "generated_this_round": [],
            }
        )
        if state:
            self.rng.setstate(tuples(state["rng"]))

    def original(self):
        if self.state["cursor"] == len(self.state["queue"]):
            self.state["queue"] = self.originals.copy()
            self.rng.shuffle(self.state["queue"])
            self.state["cursor"] = 0
            self.state["cycles"] += 1
        task = self.state["queue"][self.state["cursor"]]
        self.state["cursor"] += 1
        return {"source": "original", "task_id": task}

    def take(self, round_index, slot, pool, initial_ids):
        if round_index != self.state["round"]:
            if round_index != self.state["round"] + 1 or slot != 0:
                raise ValueError("Round transition must occur at its first slot")
            self.state.update(round=round_index, generated_this_round=[])
        candidates = [
            t for t in pool if t["task_id"] not in self.state["generated_this_round"]
        ]
        candidates.sort(
            key=lambda t: (t["task_id"] in self.state["generated_ever"], t["task_id"])
        )
        if slot % 2 and candidates:
            task = candidates[0]
            self.state["generated_this_round"].append(task["task_id"])
            if task["task_id"] not in self.state["generated_ever"]:
                self.state["generated_ever"].append(task["task_id"])
            result = {
                "source": "initial_generated"
                if task["task_id"] in initial_ids
                else "new_generated",
                "task_id": task["source_task_id"],
                "generated_task": task,
            }
        else:
            result = self.original()
        result.update(
            round=round_index, slot=slot, generated_priority_slot=bool(slot % 2)
        )
        return result

    def export(self):
        return {**copy.deepcopy(self.state), "rng": self.rng.getstate()}


def task_key(episode):
    return episode.get("generated_task_id") or episode["task_id"]


class GroupFeedback:
    """One event per complete group; past successes are captured before the group."""

    def __init__(self, settings, state=None):
        self.settings = settings
        self.state = (
            copy.deepcopy(state)
            if state
            else {
                "seen": [],
                "recent": {},
                "pattern_counts": {},
                "pattern_total": 0,
            }
        )

    def observe(self, group_id, episodes):
        if group_id in self.state["seen"]:
            raise ValueError("Group already consumed")
        self.state["seen"].append(group_id)
        if len(episodes) != 4:
            raise ValueError("A feedback group requires four trajectory slots")
        for e in episodes:
            context_for(e["task_id"], training=True)
        key = task_key(episodes[0])
        event = {
            "group_id": group_id,
            "task_key": key,
            "task_id": episodes[0]["task_id"],
            "generated_task_id": episodes[0].get("generated_task_id"),
            "policy": episodes[0]["policy"],
            "outcomes": [e["success"] for e in episodes],
            "episodes": [],
            "signals": [],
            "complete": False,
        }
        # Identity disagreements are controller errors, not all-failed signals.
        try:
            advantages = group_advantages(episodes)
        except ValueError as exc:
            event["excluded_reason"] = str(exc)
            return event
        if advantages is None:
            event["excluded_reason"] = "unknown_or_infrastructure_score"
            return event
        counts = Counter(self.state["pattern_counts"])
        patterns = set()
        for e in episodes:
            path = Path(e["directory"]) / "history.json"
            history = read_json(path)["messages"]
            trajectory_patterns = action_patterns(
                interaction_triplets(history, str(path.parent))
            )
            patterns |= trajectory_patterns
            counts.update(trajectory_patterns)
            self.state["pattern_total"] += len(trajectory_patterns)
            event["episodes"].append(
                {
                    "directory": str(path.parent),
                    "history_sha256": digest(history),
                    "success": e["success"],
                    "termination": e.get("termination"),
                    "policy": e["policy"],
                    "initial_state_hash": e["initial_state_hash"],
                }
            )
        prior = self.state["recent"].get(key, [])
        successes = [g for g in prior if any(g["outcomes"])]
        outcomes = event["outcomes"]
        if any(outcomes) and not all(outcomes):
            event["signals"].append("boundary")
        if not all(outcomes) and successes:
            event["signals"].append("forgetting")
        if not any(outcomes):
            event["signals"].append("all_failed")
        total = self.state["pattern_total"]
        rare = sorted(
            p
            for p in patterns
            if total >= self.settings["minimum_observations"]
            and counts[p] / total < self.settings["rare_percent"] / 100
        )
        if rare:
            event["signals"].append("rare")
        event.update(
            complete=True,
            rare_patterns=rare,
            prior_success_groups=successes,
            initial_state_hash=episodes[0]["initial_state_hash"],
        )
        self.state["pattern_counts"] = dict(counts)
        self.state["recent"][key] = (
            prior
            + [
                {
                    "group_id": group_id,
                    "outcomes": outcomes,
                    "policy": event["policy"],
                    "episodes": event["episodes"],
                }
            ]
        )[-self.settings["window"] :]
        return event


def select_contexts(window):
    if len(window) != 8:
        raise ValueError(
            "Feedback selection requires exactly eight completed group slots"
        )
    latest = {}
    for event in window:
        if event["complete"]:
            latest[event["task_key"]] = event

    def rank(event):
        labels = set(event["signals"])
        priority = (
            0
            if {"boundary", "forgetting"} <= labels
            else 1
            if "boundary" in labels
            else 2
            if "forgetting" in labels
            else 3
            if "all_failed" in labels
            else 4
        )
        return priority, event["task_key"]

    candidates = sorted((e for e in latest.values() if e["signals"]), key=rank)
    all_failed = [e for e in candidates if "all_failed" in e["signals"]]
    selected = all_failed[:1]
    selected += [e for e in candidates if e not in selected][: 4 - len(selected)]
    return [
        {
            **e,
            "selection_reason": "reserved_all_failed"
            if all_failed and e is all_failed[0]
            else "priority_" + str(rank(e)[0]),
        }
        for e in selected
    ]
