"""Recompute paired tool-mode results from all attempts, including failures."""

import argparse
from pathlib import Path

from .io import canonical, read_json
from .tool_modes import MODES


def summarize_comparison(directory):
    directory = Path(directory)
    config = read_json(directory / "config.json")
    selection = read_json(directory / "task_selection.json")
    attempts = [
        read_json(p) for p in sorted(directory.glob("teacher_*/trajectory.json"))
    ]
    rows = []
    for t in attempts:
        predictor = t.get("predictor", {})
        usage = t["usage"]
        discovery = sum(
            result.get("name", "").startswith("api_docs__")
            for turn in t.get("turns", [])
            for result in turn.get("tool_results", [])
        )
        rows.append(
            {
                "trajectory_id": t["trajectory_id"],
                "task_id": t["task_id"],
                "scenario_id": t.get("scenario_id"),
                "tool_mode": t["tool_mode"],
                "status": t["status"],
                "official_reward": t.get("reward"),
                "termination_reason": t["termination_reason"],
                "initial_state_hash": t.get("initial_state_hash"),
                "observed_backend": t.get("observed_backend"),
                "worker_pid": t.get("worker_pid"),
                "predictor_requests": int(predictor is not None and bool(predictor)),
                "predictor_generated_tokens": predictor.get("usage", {}).get(
                    "generated_tokens", 0
                ),
                "predictor_prompt_tokens": predictor.get("usage", {}).get(
                    "prompt_tokens", 0
                ),
                "predictor_usage_priced_cost": predictor.get("usage_priced_cost", 0),
                "actor_model_turns": usage.get("model_turns", 0),
                "initial_visible_tools": len(t.get("initial_tools", [])),
                "final_visible_tools": len(t["turns"][-1]["tools"])
                if t.get("turns")
                else 0,
                "discovery_tool_calls": discovery,
                "business_tool_calls": usage["tool_calls"] - discovery,
                "usage": usage,
            }
        )
    pairs = []
    for task in selection["tasks"]:
        members = [row for row in rows if row["task_id"] == task]
        complete = len(members) == 2 and {x["tool_mode"] for x in members} == set(MODES)
        pairs.append(
            {
                "task_id": task,
                "complete": complete,
                "same_initial_state": complete
                and len({x["initial_state_hash"] for x in members}) == 1
                and all(x["initial_state_hash"] for x in members),
                "same_backend": complete
                and len({tuple(x["observed_backend"] or []) for x in members}) == 1
                and all(x["observed_backend"] for x in members),
                "independent_workers": complete
                and len({x["worker_pid"] for x in members}) == 2
                and all(x["worker_pid"] for x in members),
            }
        )
    metrics = {}
    for mode in MODES:
        subset = [row for row in rows if row["tool_mode"] == mode]
        metrics[mode] = {
            "attempts": len(subset),
            "official_successes": sum(x["official_reward"] == 1 for x in subset),
            "infrastructure_errors": sum(x["status"] != "valid" for x in subset),
            "success_per_attempt": sum(x["official_reward"] == 1 for x in subset)
            / len(subset)
            if subset
            else None,
            **{
                key: sum(x["usage"].get(key, 0) for x in subset)
                for key in (
                    "provider_requests",
                    "tool_calls",
                    "prompt_tokens",
                    "generated_tokens",
                    "cached_prompt_tokens",
                    "usage_priced_cost",
                    "wall_seconds",
                    "unknown_cost_reservation",
                    "unknown_generation_reservation",
                )
            },
        }
    predictor_variant = config.get("predictor_prompt_variant", "official_zero_shot")
    result = {
        "format": "paired-tool-mode-pilot-v1",
        "status": "PASS"
        if pairs
        and all(
            all(
                p[k]
                for k in (
                    "complete",
                    "same_initial_state",
                    "same_backend",
                    "independent_workers",
                )
            )
            for p in pairs
        )
        and all(t["status"] == "valid" for t in attempts)
        else "PARTIAL",
        "actor_role": "teacher",
        "model": config["teacher"]["model"],
        "student_benchmark": False,
        "training_started": False,
        "request_max_tokens": config["teacher"]["max_new_tokens"],
        "budget_scope": "end_to_end_including_predictor; equal reserved allowances per attempt",
        "selection": selection,
        "pairs": pairs,
        "metrics": metrics,
        "attempts": rows,
        "limitations": [
            "Two tasks from one scenario family; no statistical superiority claim.",
            "Official zero-shot predictor component, not a full upstream benchmark reproduction.",
            "API alias not immutable; fingerprint equality is observed evidence only.",
            "Costs are usage-priced estimates, not invoices; cache/order affect latency and cost.",
        ],
    }
    # Keep historical summaries byte-equivalent when using old configurations.
    if predictor_variant != "official_zero_shot":
        result["actor_prompt_variant"] = config.get("actor_prompt_variant", "legacy_v1")
        result["predictor_prompt_variant"] = predictor_variant
        result["limitations"][1] = (
            "Project-modified Predictor prompt using the pinned upstream component; "
            "not the original official prompt or a full upstream benchmark reproduction."
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    args = parser.parse_args()
    print(canonical(summarize_comparison(args.run)))


if __name__ == "__main__":
    main()
