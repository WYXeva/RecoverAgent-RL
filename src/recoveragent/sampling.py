"""Reproducible public train sampling; outcome-selected diagnostics are explicit."""

import argparse
import hashlib
import random
from collections import defaultdict
from pathlib import Path

from .io import digest, read_json, write_json


def make_plan(task_scenarios, settings):
    """Input is the official train ID -> public generator_id mapping, not tasks."""
    if type(settings.get("seed")) is not int:
        raise ValueError("An integer sampling seed is required")
    count = settings.get("num_tasks")
    if type(count) is not int or not 1 <= count <= 20:
        raise ValueError("Select 1..20 tasks")
    excluded = set(settings.get("exclude_scenarios", []))
    available = {t: s for t, s in task_scenarios.items() if s not in excluded}
    strategy = settings.get("strategy")
    if strategy == "explicit":
        tasks = list(settings.get("task_ids", []))
        if len(tasks) != count or len(set(tasks)) != len(tasks):
            raise ValueError(
                "Explicit list must have the requested number of unique tasks"
            )
        if not set(tasks) <= available.keys():
            raise ValueError("Explicit list contains non-train or excluded tasks")
    elif strategy == "scenario_balanced":
        groups = defaultdict(list)
        for task in sorted(available):
            groups[available[task]].append(task)
        n = settings.get("num_scenarios")
        if type(n) is not int or not 1 <= n <= min(count, len(groups)):
            raise ValueError("Insufficient scenarios or invalid scenario count")
        rng = random.Random(settings["seed"])
        families = sorted(groups)
        rng.shuffle(families)
        families = families[:n]
        for family in families:
            rng.shuffle(groups[family])
        tasks = []
        while len(tasks) < count:
            before = len(tasks)
            for family in families:
                if groups[family] and len(tasks) < count:
                    tasks.append(groups[family].pop())
            if len(tasks) == before:
                raise ValueError("Selected families do not have enough distinct tasks")
    else:
        raise ValueError("Unknown sampling strategy")
    rows = [{"task_id": t, "scenario_id": task_scenarios[t]} for t in tasks]
    plan = {
        "format": "train-scenario-plan-v1",
        "split": "train",
        "settings": settings,
        "pool_sha256": digest(sorted(task_scenarios.items())),
        "pool_tasks": len(task_scenarios),
        "pool_scenarios": len(set(task_scenarios.values())),
        "tasks": tasks,
        "rows": rows,
        "num_scenarios": len({r["scenario_id"] for r in rows}),
        "selection_inputs": [
            "official train task IDs",
            "public task_id_to_generator_id",
        ],
        "hidden_labels_or_results_used": False,
    }
    diagnostic = settings.get("diagnostic_selection")
    if diagnostic is not None:
        if (
            strategy != "explicit"
            or diagnostic.get("rule") != "prior_official_failures"
            or not isinstance(diagnostic.get("source_report"), str)
            or not isinstance(diagnostic.get("source_sha256"), str)
            or len(diagnostic["source_sha256"]) != 64
        ):
            raise ValueError("Outcome-selected diagnostics need an explicit source")
        plan["selection_inputs"].append("prior official failure outcomes")
        plan["hidden_labels_or_results_used"] = True
        plan["selection_scope"] = (
            "conditional failure diagnostic, not an unbiased comparison"
        )
    return {**plan, "plan_id": digest(plan)}


def official_pool(root):
    from appworld import load_task_ids, update_root
    from appworld.task import task_id_to_generator_id

    update_root(str(Path(root).resolve()))
    # Do not use difficulty: upstream reads ground_truth/metadata.json for it.
    return {t: task_id_to_generator_id(t) for t in load_task_ids("train")}


def load_plan(config):
    plan = read_json(config["task_manifest"])
    if digest(plan) != config["task_manifest_hash"]:
        raise ValueError("Task manifest differs from the precommitted list")
    expected = make_plan(official_pool(config["appworld_root"]), config["sampling"])
    if plan != expected or len(plan["tasks"]) != config["limits"]["max_tasks"]:
        raise ValueError("Task plan cannot be reproduced from public train membership")
    diagnostic = config["sampling"].get("diagnostic_selection")
    if diagnostic:
        source = Path(diagnostic["source_report"])
        if (
            hashlib.sha256(source.read_bytes()).hexdigest()
            != diagnostic["source_sha256"]
        ):
            raise ValueError("Diagnostic selection source changed")
        failures = [
            row["task_id"]
            for row in read_json(source)["rows"]
            if row["status"] == "valid" and row["official_success"] is False
        ]
        if plan["tasks"] != failures:
            raise ValueError(
                "Diagnostic plan must include every valid prior failure once"
            )
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config = read_json(args.config)
    plan = make_plan(official_pool(config["appworld_root"]), config["sampling"])
    write_json(args.output, plan)
    print(
        f"{len(plan['tasks'])} tasks / {plan['num_scenarios']} scenarios; hash={digest(plan)}"
    )


if __name__ == "__main__":
    main()
