"""Outcome-conditioned coverage sampling; controller metadata never reaches Actor."""

from collections import Counter, defaultdict
import random

from .io import digest


def coverage_plan(pool, settings, ledger):
    if digest(ledger) != settings["coverage_ledger_hash"]:
        raise ValueError("Coverage ledger changed")
    if ledger["pool_hash"] != digest(sorted(pool.items())):
        raise ValueError("Coverage pool differs")
    successes = Counter(ledger["selected_success_counts"])
    attempts = Counter(ledger["new_attempt_counts"])
    eligible = {
        t: s
        for t, s in pool.items()
        if successes[t] < settings["max_per_task"]
        and attempts[t] < settings["max_new_attempts_per_task"]
    }
    if not 1 <= settings["num_tasks"] <= min(20, len(eligible)):
        raise ValueError("Coverage candidate count invalid or exhausted")
    rng = random.Random(settings["seed"])
    family_success = Counter()
    for t, n in successes.items():
        family_success[pool[t]] += n
    groups = defaultdict(list)
    for t, s in sorted(eligible.items()):
        groups[s].append(t)
    families = sorted(groups)
    rng.shuffle(families)

    def priority(t):
        return (successes[t] > 0, attempts[t] > 0, attempts[t])

    for members in groups.values():
        rng.shuffle(members)
        members.sort(key=priority)
    families.sort(
        key=lambda s: (
            family_success[s] > 0,
            min(priority(t) for t in groups[s]),
            family_success[s],
        )
    )
    chosen = []
    # Existing scenario-balanced round-robin behavior, now ordered by coverage.
    while len(chosen) < settings["num_tasks"]:
        for s in families:
            if groups[s] and len(chosen) < settings["num_tasks"]:
                chosen.append(groups[s].pop(0))
    rows = []
    for t in chosen:
        rows.append(
            {
                "task_id": t,
                "scenario_id": pool[t],
                "reason": "missing_family"
                if not family_success[pool[t]]
                else "missing_task_retry"
                if not successes[t] and attempts[t]
                else "missing_task"
                if not successes[t]
                else "second_trajectory_diversity",
                "prior_selected_successes": successes[t],
                "new_attempts_so_far": attempts[t],
            }
        )
    plan = {
        "format": "train-coverage-plan-v1",
        "split": "train",
        "settings": settings,
        "pool_sha256": digest(sorted(pool.items())),
        "pool_tasks": len(pool),
        "pool_scenarios": len(set(pool.values())),
        "tasks": chosen,
        "rows": rows,
        "num_scenarios": len({pool[t] for t in chosen}),
        "selection_inputs": [
            "official train IDs and public scenario mapping",
            "verified prior success/length records",
            "bounded new-attempt counts",
        ],
        "hidden_labels_or_results_used": True,
        "hidden_answers_used": False,
        "selection_scope": "outcome-conditioned coverage collection, not unbiased benchmark success rate",
    }
    return {**plan, "plan_id": digest(plan)}
