"""Recompute all-attempt scenario coverage and costs from actual collection evidence."""

import argparse
from collections import Counter
from pathlib import Path

from .demonstrations import verify_dataset, validate_receipts, sha256
from .io import digest, read_json, write_json
from .p3a import interrupted_usage
from .p3a_prompts import actor_system
from .protocol_lock import verify_lock
from .sampling import official_pool, make_plan


def summarize(run):
    run = Path(run)
    config = read_json(run / "config.json")
    pool = official_pool(config["appworld_root"])
    plan = read_json(run / "task_manifest.json")
    if (
        plan != make_plan(pool, config["sampling"])
        or digest(plan) != config["task_manifest_hash"]
    ):
        raise ValueError("Sampling plan changed or is not reproducible")
    lock_hash = verify_lock(config)
    if digest(read_json(run / "protocol_lock.json")) != lock_hash:
        raise ValueError("Frozen protocol provenance changed")
    manifest = verify_dataset(run / "clean_dataset", set(pool))
    attempts = read_json(run / "clean_dataset/attempts.json")
    rows, seen = [], set()
    for a in attempts:
        directory = run / a["trajectory_id"]
        t = read_json(directory / "trajectory.json")
        if t["status"] == "valid":
            # Audit rejected policy failures too, not only the accepted dataset.
            validate_receipts(t, directory)
        task = t["task_id"]
        if task not in plan["tasks"] or task in seen:
            raise ValueError("Unexpected/duplicate attempt in single-mode collection")
        seen.add(task)
        if t.get("scenario_id", pool[task]) != pool[task]:
            raise ValueError("Scenario differs from official generator_id")
        worker = read_json(run / (a["trajectory_id"] + ".config.json"))
        usage = interrupted_usage(directory, config["teacher"])
        for key in (
            "provider_requests",
            "prompt_tokens",
            "generated_tokens",
            "cached_prompt_tokens",
            "tool_calls",
            "usage_priced_cost",
            "unknown_cost_reservation",
            "unknown_generation_reservation",
        ):
            if abs(usage[key] - t["usage"].get(key, 0)) > 1e-8:
                raise ValueError("Receipt/usage mismatch: " + key)
        requests = sorted((directory / "requests").glob("*.request.json"))
        for request in requests:
            if request.name != "000.request.json":
                if read_json(request)["messages"][0]["content"] != actor_system(worker):
                    raise ValueError("Actor prompt changed")
        counts = Counter(
            r["name"]
            for turn in t.get("turns", [])
            for r in turn.get("tool_results", [])
        )
        rows.append(
            {
                "task_id": task,
                "scenario_id": pool[task],
                "trajectory_id": a["trajectory_id"],
                "status": t["status"],
                "official_success": a["official_success"],
                "accepted": a["accepted"],
                "filter_reasons": a["reasons"],
                "termination_reason": t["termination_reason"],
                "usage": t["usage"],
                "observed_backend": t.get("observed_backend"),
                "tool_call_counts": dict(counts),
                "public_tool_error_count": sum(
                    "Execution failed" in r["content"]
                    for turn in t.get("turns", [])
                    for r in turn.get("tool_results", [])
                ),
                "trajectory_sha256": sha256(directory / "trajectory.json"),
                "evaluation_sha256": sha256(directory / "evaluation.json")
                if (directory / "evaluation.json").exists()
                else None,
            }
        )
    rows.sort(key=lambda r: plan["tasks"].index(r["task_id"]))
    successful = [r for r in rows if r["official_success"]]
    accepted = [r for r in rows if r["accepted"]]
    valid = sum(r["status"] == "valid" for r in rows)
    families = []
    for family in dict.fromkeys(r["scenario_id"] for r in plan["rows"]):
        subset = [r for r in rows if r["scenario_id"] == family]
        families.append(
            {
                "scenario_id": family,
                "attempts": len(subset),
                "successes": sum(r["official_success"] for r in subset),
                "accepted": sum(r["accepted"] for r in subset),
            }
        )
    return {
        "status": "PASS"
        if seen == set(plan["tasks"]) and valid == len(rows)
        else "PARTIAL",
        "scope": "frozen-prompt Teacher collection, not paired A/B or Student evaluation",
        "planned_tasks": len(plan["tasks"]),
        "planned_scenarios": plan["num_scenarios"],
        "attempts": len(rows),
        "valid_evaluations": valid,
        "infrastructure_errors": len(rows) - valid,
        "unique_attempted_tasks": len(seen),
        "attempted_scenarios": len({r["scenario_id"] for r in rows}),
        "official_successes": len(successful),
        "success_per_all_attempts": len(successful) / len(rows) if rows else None,
        "success_per_valid_evaluation": len(successful) / valid if valid else None,
        "unique_successful_tasks": len({r["task_id"] for r in successful}),
        "successful_scenarios": len({r["scenario_id"] for r in successful}),
        "qualified_demonstrations": len(accepted),
        "qualified_scenarios": len({r["scenario_id"] for r in accepted}),
        "unattempted_tasks": [t for t in plan["tasks"] if t not in seen],
        "filter_reasons": dict(
            Counter(reason for r in rows for reason in r["filter_reasons"])
        ),
        "termination_reasons": dict(Counter(r["termination_reason"] for r in rows)),
        "costs": manifest["costs"],
        "worker_seconds": sum(r["usage"].get("wall_seconds", 0) for r in rows),
        "protocol_lock_hash": lock_hash,
        "sampling_plan_hash": digest(plan),
        "dataset_id": manifest["dataset_id"],
        "manifest_sha256": sha256(run / "clean_dataset/manifest.json"),
        "families": families,
        "rows": rows,
        "training_started": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = summarize(args.run)
    write_json(args.output, result)
    print(
        f"{result['official_successes']}/{result['attempts']} official successes; "
        f"{result['successful_scenarios']} successful scenarios; "
        f"{result['qualified_demonstrations']} accepted demonstrations"
    )


if __name__ == "__main__":
    main()
