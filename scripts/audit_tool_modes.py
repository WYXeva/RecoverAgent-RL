"""Offline audit of real paired receipts, public upstream prediction and budgets."""

import argparse
import hashlib
from pathlib import Path

from recoveragent.compare_tools import summarize_comparison
from recoveragent.demonstrations import sha256, verify_dataset
from recoveragent.inspect_teacher import build_view
from recoveragent.io import digest, read_json, write_json
from recoveragent.p3a import interrupted_usage
from recoveragent.teacher import normalize_message
from recoveragent.tool_modes import (
    official_predictor,
    predictor_messages,
    validate_predictor_receipts,
)


def audit(run):
    from appworld import load_task_ids, update_root
    from appworld.task import Task

    run = Path(run)
    config = read_json(run / "config.json")
    update_root(str(Path(config["appworld_root"]).resolve()))
    train = set(load_task_ids("train"))
    summary = summarize_comparison(run)
    assert summary == read_json(run / "comparison.json")
    assert summary["status"] == "PASS"
    manifest = verify_dataset(run / "clean_dataset", train)
    policy = official_predictor(
        config.get("predictor_source", ".cache/appworld-reference")
    )
    results, budgets, responses = [], set(), 0
    for path in sorted(run.glob("teacher_*/trajectory.json")):
        trajectory = read_json(path)
        worker = read_json(run / (path.parent.name + ".config.json"))
        assert trajectory["task_id"] in train
        assert worker["teacher"] == config["teacher"]
        assert worker["environment_seed"] == config["environment_seed"]
        budgets.add(
            digest(
                [
                    worker["limits"],
                    worker["remaining_requests"],
                    worker["remaining_generated_tokens"],
                    worker["remaining_cost"],
                ]
            )
        )
        actual = interrupted_usage(path.parent, config["teacher"])
        for key in (
            "provider_requests",
            "tool_calls",
            "prompt_tokens",
            "generated_tokens",
            "cached_prompt_tokens",
            "usage_priced_cost",
            "unknown_cost_reservation",
            "unknown_generation_reservation",
        ):
            assert abs(actual[key] - trajectory["usage"][key]) < 1e-9, key
        assert actual["provider_requests"] <= worker["remaining_requests"]
        assert actual["generated_tokens"] <= worker["remaining_generated_tokens"]
        if worker["limits"]["max_tool_calls"] is not None:
            assert actual["tool_calls"] <= worker["limits"]["max_tool_calls"]
        events = {t["request_id"]: t for t in trajectory["turns"]}
        if trajectory.get("predictor"):
            events["000"] = trajectory["predictor"]
        max_tokens, finishes = [], []
        for request_path in sorted((path.parent / "requests").glob("*.request.json")):
            identity = request_path.name.split(".")[0]
            request = read_json(request_path)
            response = read_json(request_path.with_name(identity + ".response.json"))
            event = events[identity]
            assert digest(request) == event["request_hash"]
            assert digest(response) == event["response_hash"]
            assert request["max_tokens"] <= 4096
            assert response["usage"]["completion_tokens"] <= request["max_tokens"]
            assert [response["model"], response["system_fingerprint"]] == trajectory[
                "observed_backend"
            ]
            if identity != "000":
                assert (
                    request["messages"]
                    == trajectory["messages"][: event["assistant_index"]]
                )
                assert request["tools"] == event["tools"]
            max_tokens.append(request["max_tokens"])
            finishes.append(response["choices"][0]["finish_reason"])
        responses += len(finishes)
        predictor = validate_predictor_receipts(trajectory, path.parent)
        if predictor:
            task = Task.load(trajectory["task_id"], load_ground_truth=False)
            assert task.ground_truth is None
            request = read_json(path.parent / "requests/000.request.json")
            response = read_json(path.parent / "requests/000.response.json")
            assert request["messages"] == predictor_messages(
                policy,
                task,
                worker.get("predictor_prompt_variant", "official_zero_shot"),
            )
            names = policy.predicted_output_to_apis(
                task, normalize_message(response)["content"]
            )
            assert names == predictor["selected_names"]
            tools = [
                d
                for d in task.api_docs.function_calling()
                if d["function"]["name"] in names
            ]
            assert tools == trajectory["initial_tools"]
        view = build_view(path.parent, all_turns=True)
        assert not any(row["receipt_warnings"] for row in view["overview"])
        assert not (view["predictor"] or {}).get("receipt_warnings")
        results.append(
            {
                "trajectory_id": path.parent.name,
                "trajectory_sha256": sha256(path),
                "evaluation_sha256": sha256(path.parent / "evaluation.json"),
                "config_sha256": sha256(run / (path.parent.name + ".config.json")),
                "terminal_checkpoint_id": trajectory["terminal_checkpoint_id"],
                "tool_mode": trajectory["tool_mode"],
                "predictor_rebuilt_from_public_task": predictor is not None,
                "requested_max_tokens": max_tokens,
                "finish_reasons": finishes,
            }
        )
    assert len(budgets) == 1
    unchanged = read_json("reports/code_manifest.json")
    assert all(
        hashlib.sha256(Path(name).read_bytes()).hexdigest() == value
        for name, value in unchanged.items()
    )
    return {
        "status": "PASS",
        "run": str(run),
        "audit_is_offline": True,
        "new_model_requests": 0,
        "training_started": False,
        "receipt_count": responses,
        "same_per_attempt_budgets": True,
        "public_only_predictor_rebuilt": True,
        "upstream_source_hash_checked": True,
        "p0_p2_unchanged_files": len(unchanged),
        "dataset_id": manifest["dataset_id"],
        "manifest_sha256": sha256(run / "clean_dataset/manifest.json"),
        "dataset_counts": manifest["counts"],
        "receipts": results,
        "source_execution_manifest_sha256": sha256(
            run / "execution_source_manifest.json"
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit(args.run)
    write_json(args.output, result)
    print(
        f"{result['status']}: audited {result['receipt_count']} responses; no paid calls"
    )


if __name__ == "__main__":
    main()
