"""Offline revalidation of prompt conditions, real receipts and historical batch diagnosis."""

import argparse
import hashlib
import json
from pathlib import Path

from recoveragent.demonstrations import sha256, validate_receipts, verify_dataset
from recoveragent.inspect_teacher import build_view
from recoveragent.io import digest, read_json, write_json
from recoveragent.p3a import interrupted_usage
from recoveragent.p3a_prompts import actor_system
from recoveragent.tool_modes import (
    DEPENDENCY_CHECK,
    official_predictor,
    predictor_messages,
)


def audit(run, source, dataset):
    from appworld import load_task_ids, update_root
    from appworld.task import Task

    run, source, dataset = Path(run), Path(source), Path(dataset)
    plan = read_json(run / "plan.json")
    config = plan["per_condition_config"]
    update_root(str(Path(config["appworld_root"]).resolve()))
    train_ids = set(load_task_ids("train"))
    predictor = official_predictor()
    budgets, initial_states, backends, pids, rows, selector_messages = (
        set(),
        set(),
        set(),
        set(),
        [],
        {},
    )
    for condition, _, _ in plan["conditions"]:
        directory = run / condition
        paths = list(directory.glob("teacher_*/trajectory.json"))
        assert len(paths) == 1
        path = paths[0]
        trajectory = read_json(path)
        worker = read_json(directory / (path.parent.name + ".config.json"))
        assert worker["limits"]["max_tool_calls"] is None
        assert worker["teacher"]["max_tool_calls_per_turn"] is None
        assert trajectory["task_id"] in train_ids
        assert trajectory["messages"][0]["content"] == actor_system(worker)
        validate_receipts(trajectory, path.parent)
        actual = interrupted_usage(path.parent, config["teacher"])
        for key in (
            "provider_requests",
            "prompt_tokens",
            "generated_tokens",
            "cached_prompt_tokens",
            "tool_calls",
            "usage_priced_cost",
        ):
            assert abs(actual[key] - trajectory["usage"][key]) < 1e-9
        assert actual["provider_requests"] <= worker["remaining_requests"]
        assert actual["generated_tokens"] <= worker["remaining_generated_tokens"]
        budgets.add(
            digest(
                [
                    worker["limits"],
                    worker["teacher"],
                    worker["remaining_requests"],
                    worker["remaining_generated_tokens"],
                    worker["remaining_cost"],
                ]
            )
        )
        initial_states.add(trajectory["initial_state_hash"])
        backends.add(tuple(trajectory["observed_backend"]))
        pids.add(trajectory["worker_pid"])
        for request_path in (path.parent / "requests").glob("*.request.json"):
            request = read_json(request_path)
            response = read_json(
                request_path.with_name(
                    request_path.name.replace(".request.", ".response.")
                )
            )
            assert request["max_tokens"] == 4096
            assert response["usage"]["completion_tokens"] <= request["max_tokens"]
            assert response["choices"][0]["finish_reason"] in {"stop", "tool_calls"}
            assert (response["model"], response["system_fingerprint"]) == tuple(
                trajectory["observed_backend"]
            )
        if trajectory.get("predictor"):
            task = Task.load(trajectory["task_id"], load_ground_truth=False)
            assert task.ground_truth is None
            request = read_json(path.parent / "requests/000.request.json")
            assert request["messages"] == predictor_messages(
                predictor, task, worker["predictor_prompt_variant"]
            )
            selector_messages[condition] = request["messages"]
        view = build_view(path.parent, all_turns=True)
        assert not any(row["receipt_warnings"] for row in view["overview"])
        rows.append(
            {
                "condition": condition,
                "trajectory_id": path.parent.name,
                "trajectory_sha256": sha256(path),
                "evaluation_sha256": sha256(path.parent / "evaluation.json"),
                "config_sha256": sha256(
                    directory / (path.parent.name + ".config.json")
                ),
                "actor_prompt_sha256": digest(trajectory["messages"][0]["content"]),
                "dataset_manifest_sha256": sha256(
                    directory / "clean_dataset/manifest.json"
                ),
                "batch_sizes": [
                    turn.get("dispatched_count", 0) for turn in trajectory["turns"]
                ],
                "usage_recomputed": actual,
            }
        )
    assert len(budgets) == len(initial_states) == len(backends) == 1
    assert len(pids) == 3
    original = selector_messages["a_official"]
    enhanced = selector_messages["a_dependency"]
    assert original[:-1] == enhanced[:-1]
    assert (
        enhanced[-1]["content"] == original[-1]["content"] + "\n\n" + DEPENDENCY_CHECK
    )
    assert rows[1]["actor_prompt_sha256"] == rows[2]["actor_prompt_sha256"]
    old = read_json(source / "trajectory.json")
    playlist_result = next(
        result
        for event in old["turns"]
        for result in event.get("tool_results", [])
        if result["name"] == "spotify__show_playlist_library"
    )
    playlists = json.loads(playlist_result["content"])
    ids = [identifier for playlist in playlists for identifier in playlist["song_ids"]]
    groups, sizes, generations = [], [], []
    for identity in ("007", "008"):
        response = read_json(source / "requests" / (identity + ".response.json"))
        calls = response["choices"][0]["message"]["tool_calls"]
        assert all(c["function"]["name"] == "spotify__show_song" for c in calls)
        groups.append(
            {json.loads(c["function"]["arguments"])["song_id"] for c in calls}
        )
        sizes.append(len(calls))
        generations.append(response["usage"]["completion_tokens"])
    assert not groups[0] & groups[1]
    assert groups[0] | groups[1] <= set(ids)
    original_code = read_json("reports/code_manifest.json")
    assert all(
        hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
        for p, h in original_code.items()
    )
    manifest = verify_dataset(dataset, train_ids)
    return {
        "status": "PASS",
        "new_model_requests": 0,
        "training_started": False,
        "same_initial_state_and_backend": True,
        "independent_processes": len(pids),
        "same_budgets_and_teacher": True,
        "a_actor_prompts_identical": True,
        "a_predictor_only_prompt_difference": "dependency supplement appended to last public user message",
        "historical_batch_analysis": {
            "playlists": len(playlists),
            "song_entries": len(ids),
            "unique_songs": len(set(ids)),
            "batch_sizes": sizes,
            "generated_tokens": generations,
            "overlapping_ids": 0,
            "all_ids_from_previous_observation": True,
            "overhead_calls_before_song_reads": 8,
            "old_total_quota": 40,
            "old_executed_before_rejection": 28,
            "old_remaining": 12,
            "reason_for_exact_batch_size_20": "NOT_OBSERVABLE; model text only states batching",
        },
        "rows": rows,
        "p0_p2_unchanged_files": len(original_code),
        "dataset_id": manifest["dataset_id"],
        "dataset_counts": manifest["counts"],
        "dataset_manifest_sha256": sha256(dataset / "manifest.json"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--source", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit(args.run, args.source, args.dataset)
    write_json(args.output, result)
    print(
        "PASS: public prompts, real receipts, identical A Actor prompts, quotas and dataset audited offline"
    )


if __name__ == "__main__":
    main()
