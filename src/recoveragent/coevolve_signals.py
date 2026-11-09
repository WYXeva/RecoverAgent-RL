"""CoEvolve §3.1 feedback over completed, train-only interaction evidence.

The paper does not publish W, N_min, or an action-pattern implementation.
Those choices are explicit configuration, rather than claimed upstream defaults.
"""

from collections import Counter, defaultdict, deque
import copy
from pathlib import Path

from .io import digest, read_json
from .v2_tasks import context_for


def interaction_triplets(messages, rollout_id):
    """Keep real complete call/results, including observable failed actions."""
    rows = []
    for i, message in enumerate(messages):
        for j, call in enumerate(message.get("tool_calls", [])):
            if message["role"] != "assistant" or i + j + 1 >= len(messages):
                raise ValueError("Incomplete exploration boundary")
            result = messages[i + j + 1]
            if result.get("role") != "tool" or result.get("tool_call_id") != call["id"]:
                raise ValueError("Exploration action/result does not match")
            rows.append(
                {
                    "rollout_id": rollout_id,
                    "action_index": len(rows),
                    "action": copy.deepcopy(call["function"]),
                    "observation": result["content"],
                    "call_id": call["id"],
                    "failed": "Execution failed" in result["content"],
                }
            )
    return rows


def action_patterns(triplets):
    """Documented API-name unigram/bigram choice; no IDs, answers or scorer text."""
    names = [
        r["action"]["name"]
        for r in triplets
        if not r["action"]["name"].startswith(("api_docs__", "supervisor__"))
    ]
    return set(names) | {a + " -> " + b for a, b in zip(names, names[1:])}


class SignalLedger:
    def __init__(self, *, window, minimum_observations, rare_percent):
        if window < 1 or minimum_observations < 1 or not 0 < rare_percent < 100:
            raise ValueError("Invalid signal extraction settings")
        self.window = window
        self.minimum_observations = minimum_observations
        self.rare_percent = rare_percent
        self.recent = defaultdict(lambda: deque(maxlen=window))
        self.pattern_counts = Counter()
        self.pattern_total = 0
        self.seen = set()
        self.events = []

    def observe(self, episodes, *, group=False):
        if not episodes:
            return []
        if (
            group
            and len(
                {
                    digest(
                        {
                            k: r[k]
                            for k in (
                                "task_id",
                                "generated_task_id",
                                "policy",
                                "initial_state_hash",
                            )
                        }
                    )
                    for r in episodes
                }
            )
            != 1
        ):
            raise ValueError(
                "Boundary signal requires one task, policy and initial state"
            )
        usable = [
            r
            for r in episodes
            if r["classification"] != "infrastructure_error"
            and type(r["success"]) is bool
        ]
        boundary = (
            group
            and len(usable) == len(episodes)
            and {r["success"] for r in usable} == {True, False}
        )
        prepared = []
        for result in usable:
            context_for(result["task_id"], training=True)
            directory = Path(result["directory"])
            key = str(directory.resolve())
            if key in self.seen:
                raise ValueError("Feedback trajectory already consumed")
            self.seen.add(key)
            history = read_json(directory / "history.json")["messages"]
            triplets = interaction_triplets(history, key)
            patterns = action_patterns(triplets)
            self.pattern_counts.update(patterns)
            self.pattern_total += len(patterns)
            prepared.append((result, key, history, patterns))
        events = []
        prior = {k: list(v) for k, v in self.recent.items()}
        for result, key, history, patterns in prepared:
            task = result.get("generated_task_id") or result["task_id"]
            labels = []
            if not result["success"] and any(prior.get(task, [])):
                labels.append("forgetting")
            if boundary:
                labels.append("boundary")
            rare = sorted(
                p
                for p in patterns
                if self.pattern_total >= self.minimum_observations
                and self.pattern_counts[p] / self.pattern_total
                < self.rare_percent / 100
            )
            if rare:
                labels.append("rare")
            self.recent[task].append(result["success"])
            if labels:
                event = {
                    "task_id": result["task_id"],
                    "generated_task_id": result.get("generated_task_id"),
                    "directory": key,
                    "signals": labels,
                    "rare_patterns": rare,
                    "policy": result["policy"],
                    "history_sha256": digest(history),
                    "outcome": result["success"],
                    "termination": result.get("termination"),
                    "signal_statistics": {
                        "pattern_total": self.pattern_total,
                        "pattern_counts": dict(self.pattern_counts),
                        "recent_outcomes": list(self.recent[task]),
                    },
                }
                events.append({**event, "event_id": digest(event)})
        self.events.extend(events)
        return events

    def export(self):
        return {
            "window": self.window,
            "minimum_observations": self.minimum_observations,
            "rare_percent": self.rare_percent,
            "pattern_definition": "unique_business_api_unigrams_and_bigrams_per_trajectory",
            "rare_rule": "N>=N_min and c_p>0 and c_p/N<theta/100; paper equation",
            "pattern_counts": dict(self.pattern_counts),
            "pattern_total": self.pattern_total,
            "recent_outcomes": {k: list(v) for k, v in self.recent.items()},
            "consumed_directories": sorted(self.seen),
            "events": self.events,
        }
