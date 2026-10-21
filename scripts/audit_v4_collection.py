"""Controller-side all-attempt A/B audit. Export metadata only, no raw task text."""

import argparse
from collections import Counter
import json
from pathlib import Path

from recoveragent.compare_tools import summarize_comparison
from recoveragent.demonstrations import sha256, verify_dataset
from recoveragent.execution_v4 import actor_system
from recoveragent.io import digest, read_json, write_json
from recoveragent.p3a import interrupted_usage
from recoveragent.protocol_lock import verify_lock
from recoveragent.sampling import load_plan
from recoveragent.tool_modes import official_predictor, predictor_messages
from recoveragent.validated_selection import dependency_catalogue
from scripts.analyze_failure_retry import check_receipts_including_rejected_tail


def audit(run, system_factory=actor_system):
    from appworld.task import Task

    run = Path(run)
    config = read_json(run / "config.json")
    plan = load_plan(config)
    if (
        plan != read_json(run / "task_manifest.json")
        or plan["hidden_labels_or_results_used"]
    ):
        raise ValueError("New A/B tasks must be preselected without outcomes")
    if digest(read_json(run / "protocol_lock.json")) != verify_lock(config):
        raise ValueError("Protocol source changed")
    from recoveragent.sampling import official_pool

    manifest = verify_dataset(
        run / "clean_dataset", set(official_pool(config["appworld_root"]))
    )
    comparison = summarize_comparison(run)
    accepted = read_json(run / "clean_dataset/attempts.json")
    by_id = {a["trajectory_id"]: a for a in accepted}
    rows, keys = [], set()
    predictor = official_predictor()
    for path in sorted(run.glob("teacher_*/trajectory.json")):
        t = read_json(path)
        directory = path.parent
        worker = read_json(run / (directory.name + ".config.json"))
        key = (t["task_id"], t["tool_mode"])
        if key in keys or t["task_id"] not in plan["tasks"]:
            raise ValueError("Duplicate/unplanned attempt")
        keys.add(key)
        actual = interrupted_usage(directory, config["teacher"])
        for field, value in actual.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if abs(value - t["usage"].get(field, 0)) > 1e-8:
                    raise ValueError("Raw cost/usage differs: " + field)
        if t["status"] == "valid":
            check_receipts_including_rejected_tail(t, directory)
        public = Task.load(t["task_id"], load_ground_truth=False)
        expected_task = {
            "instruction": public.instruction,
            "supervisor": dict(public.supervisor),
            "datetime": str(public.datetime),
            "app_descriptions": public.app_descriptions,
        }
        for request in sorted((directory / "requests").glob("*.request.json")):
            payload = read_json(request)
            if "tools" in payload:
                if (
                    payload["messages"][0]["content"] != system_factory(worker)
                    or json.loads(payload["messages"][1]["content"]) != expected_task
                ):
                    raise ValueError(
                        "Actor prompt/task differs from public initial inputs"
                    )
                if payload["max_tokens"] > 4096:
                    raise ValueError("Per-reply bound changed")
        prediction = t.get("predictor", {})
        if prediction:
            original = read_json(directory / "requests/000.request.json")
            if original["messages"] != predictor_messages(
                predictor, public, config["predictor_prompt_variant"]
            ):
                raise ValueError("Predictor context not reproducible from public task")
            if prediction["dependency_catalogue"] != dependency_catalogue(
                public.api_docs
            ):
                raise ValueError(
                    "Dependency catalogue differs from official public docs"
                )
        evaluation = (
            read_json(directory / "evaluation.json")
            if (directory / "evaluation.json").exists()
            else None
        )
        failures = evaluation["official"]["failures"] if evaluation else []
        answer_failures = [
            f for f in failures if f["requirement"].strip() == "assert answers match."
        ]
        calls = [
            c["function"]
            for m in t.get("messages", [])
            for c in m.get("tool_calls", [])
        ]
        completions = [
            json.loads(c["arguments"])
            for c in calls
            if c["name"] == "supervisor__complete_task"
        ]
        numeric_answer_rejected = False
        if completions and any(
            "normalize=float" in f["trace"] for f in answer_failures
        ):
            try:
                float(completions[-1].get("answer"))
            except (ValueError, TypeError):
                numeric_answer_rejected = True
        tool_results = [m for m in t.get("messages", []) if m["role"] == "tool"]
        record = by_id[t["trajectory_id"]]
        rows.append(
            {
                "task_id": t["task_id"],
                "scenario_id": public.generator_id,
                "tool_mode": t["tool_mode"],
                "trajectory_id": t["trajectory_id"],
                "status": t["status"],
                "official_success": t.get("reward") == 1,
                "accepted": record["accepted"],
                "filter_reasons": record["reasons"],
                "termination_reason": t["termination_reason"],
                "failed_assertions": len(failures) if evaluation else None,
                "answer_failed_assertions": len(answer_failures)
                if evaluation
                else None,
                "non_answer_failed_assertions": len(failures) - len(answer_failures)
                if evaluation
                else None,
                "answer_key_present": "answer" in completions[-1]
                if completions
                else None,
                "answer_fails_official_float_normalization": numeric_answer_rejected,
                "public_tool_error_count": sum(
                    "Execution failed" in m.get("content", "") for m in tool_results
                ),
                "selector_corrections": max(0, len(prediction.get("attempts", [])) - 1),
                "initial_dependency_errors": prediction.get("attempts", [{}])[0].get(
                    "dependency_errors", []
                ),
                "usage": t["usage"],
                "trajectory_sha256": sha256(path),
                "evaluation_sha256": sha256(directory / "evaluation.json")
                if evaluation
                else None,
            }
        )
    rows.sort(key=lambda row: (plan["tasks"].index(row["task_id"]), row["tool_mode"]))
    for mode, metric in comparison["metrics"].items():
        subset = [r for r in rows if r["tool_mode"] == mode]
        for label, field in (
            ("successful", "official_success"),
            ("accepted", "accepted"),
        ):
            chosen = [r for r in subset if r[field]]
            metric[label + "_trajectories"] = len(chosen)
            metric[label + "_unique_tasks"] = len({r["task_id"] for r in chosen})
            metric[label + "_families"] = len({r["scenario_id"] for r in chosen})
        metric["selector_corrections"] = sum(r["selector_corrections"] for r in subset)
    good = [r for r in rows if r["accepted"]]
    return {
        "status": comparison["status"],
        "run_id": run.name,
        "comparison": comparison,
        "rows": rows,
        "coverage": {
            "attempted_tasks": len({r["task_id"] for r in rows}),
            "attempted_families": len({r["scenario_id"] for r in rows}),
            "accepted_trajectories": len(good),
            "accepted_unique_tasks": len({r["task_id"] for r in good}),
            "accepted_families": len({r["scenario_id"] for r in good}),
        },
        "filter_reason_counts": dict(
            Counter(reason for r in rows for reason in r["filter_reasons"])
        ),
        "dataset_manifest_sha256": sha256(run / "clean_dataset/manifest.json"),
        "dataset_counts": manifest["counts"],
        "costs": manifest["costs"],
        "source_lock_hash": verify_lock(config),
        "task_manifest_hash": digest(plan),
        "public_inputs_reconstructed": True,
        "all_raw_receipts_reconciled": True,
        "no_actor_feedback_from_evaluator": True,
        "training_started": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit(args.run)
    write_json(args.output, result)
    print({k: result[k] for k in ("status", "coverage", "costs")})


if __name__ == "__main__":
    main()
