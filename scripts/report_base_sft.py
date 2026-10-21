"""Recompute public dataset metrics from private artifacts, without exporting content."""

import argparse
from collections import Counter
from pathlib import Path

from recoveragent.demonstrations import sha256
from recoveragent.formal_dataset import json_lines, verify
from recoveragent.io import read_json, write_json
from scripts.audit_base_sft_collection import audit


def metrics(dataset, campaign_result, expected):
    dataset = Path(dataset)
    verification = verify(dataset, expected)
    manifest = read_json(dataset / "manifest.json")
    result = read_json(campaign_result)
    audits = [audit(run) for run in result["completed_runs"]]
    attempts = [row for a in audits for row in a["rows"]]
    quality = {r["trajectory_id"]: r for r in result["quality_rows"]}
    selection = read_json(dataset / "control/selection.json")
    weights = read_json(dataset / "control/example_weights.json")
    split = read_json(dataset / "control/split.json")
    records = [
        r
        for name in ("train", "validation")
        for r in json_lines(dataset / "model_input" / f"{name}.jsonl")
    ]
    apps = {
        s["trajectory_id"]: sorted(
            {
                c["function"]["name"].split("__")[0]
                for message in s["messages"]
                for c in message.get("tool_calls", [])
                if c["function"]["name"].split("__")[0]
                not in {"api_docs", "supervisor"}
            }
        )
        for s in records
    }
    new_costs = Counter()
    for a in audits:
        if a["costs"]["currency"] != "USD":
            raise ValueError("Do not aggregate different currencies")
        for key, value in a["costs"].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                new_costs[key] += value
    new_costs["total_tokens"] = (
        new_costs["prompt_tokens"] + new_costs["generated_tokens"]
    )
    families = []
    for family, partition in sorted(split.items()):
        ss = [s for s in records if s["scenario_id"] == family]
        counts = Counter()
        for s in ss:
            counts.update(quality[s["trajectory_id"]]["stats"])
        denominator = manifest["partition_metrics"][partition]["ready_token_counts"][
            "examples"
        ]
        families.append(
            {
                "scenario_id": family,
                "business_apps": sorted(
                    {app for s in ss for app in apps[s["trajectory_id"]]}
                ),
                "partition": partition,
                "trajectories": len(ss),
                "tasks": len({s["task_id"] for s in ss}),
                "stats": dict(counts),
                "effective_loss_mass": sum(
                    weights[s["trajectory_id"]]
                    * quality[s["trajectory_id"]]["stats"]["examples"]
                    for s in ss
                )
                / denominator,
            }
        )
    total = Counter()
    for p in manifest["partition_metrics"].values():
        total.update(p["ready_token_counts"])
    longest_prefix = max(
        e["target_start"]
        for name in ("train", "validation")
        for e in json_lines(dataset / "tokenized" / f"{name}.jsonl")
    )
    good = [a for a in attempts if a["accepted"]]
    valid = [a for a in attempts if a["status"] == "valid"]
    successes = sum(a["official_success"] for a in attempts)
    guards = [a["answer_guard"] for a in attempts if a["answer_guard"]]
    initial = read_json(result["campaign"]["existing_inventory"] + "/inventory.json")
    historical_attempts = read_json(
        result["campaign"]["existing_inventory"] + "/attempts.json"
    )
    repeat_costs = Counter()
    repeats = [a for a in attempts if a["new_attempt_number"] > 1]
    for a in repeats:
        for k in (
            "provider_requests",
            "prompt_tokens",
            "generated_tokens",
            "tool_calls",
            "usage_priced_cost",
        ):
            repeat_costs[k] += a["usage"].get(k, 0)
    source = read_json(dataset / "control/source_inventory.json")
    targets = {
        "trajectories": len(records) >= 120,
        "tasks": len({s["task_id"] for s in records}) >= 60,
        "families": len({s["scenario_id"] for s in records}) >= 20,
    }
    task_counts = Counter(s["task_id"] for s in records)
    attempt_counts = Counter(a["task_id"] for a in attempts)
    pool = read_json(dataset / "control/official_train_pool.json")
    eligible = [t for t in pool if task_counts[t] < 2 and attempt_counts[t] < 2]
    stop_reason = (
        result["interruption"]["reason"]
        if result.get("interruption")
        else "target_reached_at_batch_boundary"
        if all(targets.values())
        else "eligible_pool_exhausted"
        if not eligible
        else "campaign_batch_limit"
        if len(audits) == result["campaign"]["max_batches"]
        else "unexpected_early_stop"
    )
    if stop_reason == "unexpected_early_stop":
        raise ValueError("Collection stopped before a declared boundary")
    diagnostic = read_json("reports/base_sft_provider_diagnostic.json")
    combined_costs = dict(source["costs_by_currency"]["USD"])
    combined_costs["provider_requests"] += diagnostic["model_requests"]
    for k in (
        "usage_priced_cost",
        "unknown_cost_reservation",
        "unknown_generation_reservation",
    ):
        combined_costs[k] = combined_costs.get(k, 0) + diagnostic.get(k, 0)
    for k in ("prompt_tokens", "generated_tokens", "cached_prompt_tokens"):
        combined_costs[k] += diagnostic.get("usage", {}).get(k, 0)
    if (
        len({a["trajectory_id"] for a in attempts}) != len(attempts)
        or source["unique_attempts"] != initial["unique_attempts"] + len(attempts)
        or len(records) != manifest["counts"]["selected"]
        or max(Counter(s["task_id"] for s in records).values()) > 2
        or source["unique_qualified_trajectories"]
        != initial["unique_qualified_trajectories"] + len(good)
    ):
        raise ValueError("Aggregated candidate/attempt/task accounting mismatch")
    return {
        "status": "PASS",
        "dataset_verification": verification,
        "initial_inventory": initial,
        "initial_quality": read_json(result["campaign"]["existing_quality"]),
        "new_collection": {
            "stop_reason": stop_reason,
            "remaining_eligible_tasks": len(eligible),
            "batches": len(audits),
            "attempts": len(attempts),
            "distinct_attempted_tasks": len({a["task_id"] for a in attempts}),
            "previously_unattempted_tasks": len(
                {a["task_id"] for a in attempts}
                - {a["task_id"] for a in historical_attempts}
            ),
            "distinct_attempted_families": len({a["scenario_id"] for a in attempts}),
            "valid_attempts": len(valid),
            "infrastructure_errors": len(attempts) - len(valid),
            "requests_without_response": sum(
                a["requests_without_response"] for a in attempts
            ),
            "official_successes": successes,
            "qualified": len(good),
            "qualified_tasks": len({a["task_id"] for a in good}),
            "new_success_tasks_over_history": len(
                {a["task_id"] for a in good}
                - {a["task_id"] for a in historical_attempts if a["accepted"]}
            ),
            "qualified_families": len({a["scenario_id"] for a in good}),
            "success_per_attempt": successes / len(attempts),
            "success_per_valid": successes / len(valid) if valid else None,
            "filter_reasons_nonexclusive": dict(
                Counter(r for a in attempts for r in a["filter_reasons"])
            ),
            "termination_reasons": dict(
                Counter(a["termination_reason"] for a in attempts)
            ),
            "costs": {**dict(new_costs), "currency": "USD", "billed_cost": None},
            "controller_wall_seconds": sum(
                read_json(Path(r) / "summary.json")["wall_seconds"]
                for r in result["completed_runs"]
            ),
            "backend_rollout_counts": dict(
                Counter(str(a["observed_backend"]) for a in attempts)
            ),
            "guard_corrections": sum(g["correction_used"] for g in guards),
            "guard_valid_corrections": sum(
                g["correction_valid"] is True for g in guards
            ),
            "official_success_after_guard_correction": sum(
                bool(
                    a["answer_guard"]
                    and a["answer_guard"]["correction_used"]
                    and a["official_success"]
                )
                for a in attempts
            ),
            "selection_scope": "outcome-conditioned coverage, not unbiased overall success rate",
            "second_new_attempts": len(repeats),
            "second_attempt_costs": dict(repeat_costs),
        },
        "source_inventory": source,
        "provider_diagnostic": diagnostic,
        "all_historical_and_new_and_diagnostic_costs_usd": combined_costs,
        "interruption": result.get("interruption"),
        "selection": selection,
        "final_counts": manifest["counts"],
        "partition_metrics": manifest["partition_metrics"],
        "family_distribution": families,
        "total_token_counts": dict(total),
        "longest_prefix_tokens": longest_prefix,
        "longest_sequence_tokens": max(
            p["max_sequence_tokens"] for p in manifest["partition_metrics"].values()
        ),
        "longest_target_tokens": max(
            p["max_target_tokens"] for p in manifest["partition_metrics"].values()
        ),
        "masked_token_fraction": total["context_tokens"] / total["input_tokens"],
        "batch_target_fraction": total["batch_targets"] / total["examples"],
        "input_expansion_over_final_sequences": total["input_tokens"]
        / sum(
            quality[s["trajectory_id"]]["last_turn_sequence_tokens"] for s in records
        ),
        "selected_prompt_versions": dict(
            Counter(s.get("actor_prompt_variant", "legacy_v1") for s in records)
        ),
        "selected_tool_modes": dict(
            Counter(s.get("tool_mode", "dynamic_discovery") for s in records)
        ),
        "selected_app_presence": dict(
            Counter(app for values in apps.values() for app in values)
        ),
        "selected_per_task": dict(Counter(s["task_id"] for s in records)),
        "all_attempt_audits": audits,
        "targets_met": targets,
        "training_started": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True)
    p.add_argument("--campaign-result", required=True)
    p.add_argument("--expected-manifest-sha256", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    result = metrics(a.dataset, a.campaign_result, a.expected_manifest_sha256)
    write_json(a.output, result)
    print(
        {
            "metrics_sha256": sha256(a.output),
            "final": result["final_counts"],
            "new_attempts": result["new_collection"]["attempts"],
        }
    )
