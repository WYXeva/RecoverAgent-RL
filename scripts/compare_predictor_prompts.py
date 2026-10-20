"""Three small real Teacher runs: discovery, official predictor, dependency supplement."""

import argparse
import copy
import json
from pathlib import Path

from recoveragent.io import read_json, write_json
from recoveragent.p3a import collect, validate_config

CONDITIONS = (
    ("b_dynamic", "dynamic_discovery", "official_zero_shot"),
    ("a_official", "api_predictor", "official_zero_shot"),
    ("a_dependency", "api_predictor", "dependency_aware_v1"),
)


def compare(config, output, credentials_file):
    validate_config(config)
    if (
        config["limits"]["max_tasks"] != 1
        or config["limits"]["max_tool_calls"] is not None
        or config["teacher"]["max_tool_calls_per_turn"] is not None
        or config.get("actor_prompt_variant") != "budget_batched_v2"
    ):
        raise ValueError(
            "This diagnostic requires one task, no tool quotas and the common v2 Actor prompt"
        )
    for key, total_cap in (
        ("max_requests", 80),
        ("total_generated_tokens", 32768),
        ("total_wall_seconds", 1800),
    ):
        if len(CONDITIONS) * config["limits"][key] > total_cap:
            raise ValueError(
                "Three-condition diagnostic exceeds the serial pilot budget"
            )
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "plan.json",
        {
            "conditions": CONDITIONS,
            "per_condition_config": config,
            "scope": "first official train task; one fresh original-state run per condition",
            "predefined_order": True,
            "training_started": False,
        },
    )
    results, backend = [], None
    for name, mode, variant in CONDITIONS:
        arm = copy.deepcopy(config)
        arm.update(tool_mode=mode, predictor_prompt_variant=variant)
        if backend:
            arm["expected_backend"] = backend
        directory = output / name
        summary = collect(arm, directory, credentials_file)
        paths = list(directory.glob("teacher_*/trajectory.json"))
        if len(paths) != 1:
            results.append({"condition": name, "status": "BLOCKED", "summary": summary})
            break
        trajectory = read_json(paths[0])
        backend = trajectory.get("observed_backend") or backend
        results.append(
            {
                "condition": name,
                "task_id": trajectory["task_id"],
                "trajectory_id": trajectory["trajectory_id"],
                "path": str(paths[0].parent),
                "status": trajectory["status"],
                "reward": trajectory.get("reward"),
                "termination_reason": trajectory["termination_reason"],
                "initial_state_hash": trajectory.get("initial_state_hash"),
                "observed_backend": trajectory.get("observed_backend"),
                "actor_prompt_variant": trajectory.get("actor_prompt_variant"),
                "predictor_prompt_variant": variant,
                "selected_names": trajectory.get("predictor", {}).get("selected_names"),
                "usage": trajectory["usage"],
                "clean_dataset_counts": summary["counts"],
            }
        )
        if trajectory["status"] != "valid":
            break
    comparable = (
        len(results) == len(CONDITIONS)
        and all(x["status"] == "valid" for x in results)
        and len({x.get("initial_state_hash") for x in results}) == 1
        and len({tuple(x.get("observed_backend") or []) for x in results}) == 1
    )
    result = {
        "status": "PASS" if comparable else "PARTIAL",
        "comparison_scope": "one-task prompt diagnostic; no statistical superiority claim",
        "training_started": False,
        "results": results,
        "costs": {
            key: sum(x.get("usage", {}).get(key, 0) for x in results)
            for key in (
                "provider_requests",
                "prompt_tokens",
                "generated_tokens",
                "cached_prompt_tokens",
                "tool_calls",
                "usage_priced_cost",
                "wall_seconds",
            )
        },
    }
    write_json(output / "comparison.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--credentials-file", required=True)
    args = parser.parse_args()
    print(
        json.dumps(compare(read_json(args.config), args.output, args.credentials_file))
    )


if __name__ == "__main__":
    main()
