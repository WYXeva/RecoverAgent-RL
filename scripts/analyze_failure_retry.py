"""Controller-only failure diagnosis and conditional A retry audit.

Only metadata/counts/hashes leave this report; hidden evaluator traces remain in
local run directories. Nothing here is an Actor tool or a prompt input.
"""

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import re

from recoveragent.demonstrations import sha256, validate_receipts
from recoveragent.io import digest, read_json, write_json
from recoveragent.p3a_prompts import actor_system
from recoveragent.p3a import interrupted_usage
from recoveragent.sampling import load_plan
from recoveragent.teacher import normalize_message, response_usage, usage_cost
from jsonschema import ValidationError
from recoveragent.tool_modes import official_predictor, predictor_messages


def recommendation_counts(messages):
    seen, artists, pages = set(), Counter(), []
    names, multi = {}, 0
    for message in messages:
        if (
            message.get("role") != "tool"
            or message.get("name") != "spotify__show_recommendations"
        ):
            continue
        songs = json.loads(message["content"])
        if not isinstance(songs, list):
            continue
        pages.append(len(songs))
        for song in songs:
            if song["song_id"] in seen:
                continue
            seen.add(song["song_id"])
            ids = {a["id"] for a in song["artists"]}
            multi += len(ids) > 1
            artists.update(ids)
            names.update({a["id"]: a["name"] for a in song["artists"]})
    return seen, artists, pages, names, multi


def check_receipts_including_rejected_tail(t, directory, validate_batch=None):
    """A rejected response has no Assistant message; verify it separately.

    Existing complete-trajectory verification remains unchanged. The temporary
    prefix copy is used only to check receipts, never exported or rated successful.
    """
    actual = interrupted_usage(directory, t["teacher"])
    for key in [
        "provider_requests",
        "tool_calls",
        "prompt_tokens",
        "generated_tokens",
        "cached_prompt_tokens",
        "usage_priced_cost",
        "unknown_generation_reservation",
        "unknown_cost_reservation",
    ]:
        assert abs(actual[key] - t["usage"][key]) < 1e-8
    bad = [i for i, turn in enumerate(t["turns"]) if "format_error" in turn]
    if not bad:
        validate_receipts(t, directory)
        return
    assert (
        bad == [len(t["turns"]) - 1] and t["termination_reason"] == "invalid_completion"
    )
    turn = t["turns"][-1]
    request = read_json(directory / "requests" / (turn["request_id"] + ".request.json"))
    response = read_json(
        directory / "requests" / (turn["request_id"] + ".response.json")
    )
    assert (
        digest(request) == turn["request_hash"]
        and digest(response) == turn["response_hash"]
    )
    assert request["messages"] == t["messages"] and request["tools"] == turn["tools"]
    assert turn["assistant_index"] == len(t["messages"]) and not turn.get(
        "dispatched_count", 0
    )
    assert response["model"] == request["model"] == t["teacher"]["model"]
    assert response_usage(response) == turn["usage"]
    try:
        message = normalize_message(response, t["teacher"]["max_tool_calls_per_turn"])
        if validate_batch is not None:
            validate_batch(message, request)
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        if turn["format_error"] != type(exc).__name__ + ": " + str(exc):
            raise ValueError("Recorded rejection reason differs from replay") from exc
    else:
        raise ValueError("Recorded rejection does not match the actual response")
    prefix = copy.deepcopy(t)
    prefix["turns"].pop()
    prefix["usage"]["provider_requests"] -= 1
    prefix["usage"]["model_turns"] -= 1
    for key, value in turn["usage"].items():
        prefix["usage"][key] -= value
    prefix["usage"]["usage_priced_cost"] -= usage_cost(
        turn["usage"], t["teacher"]["pricing"]
    )
    validate_receipts(prefix, directory)


def diagnose(directory):
    directory = Path(directory)
    t, e = (
        read_json(directory / "trajectory.json"),
        read_json(directory / "evaluation.json"),
    )
    check_receipts_including_rejected_tail(t, directory)
    completions = [
        (i, c["function"])
        for i, m in enumerate(t["messages"])
        for c in m.get("tool_calls", [])
        if c["function"]["name"] == "supervisor__complete_task"
    ]
    answer, answer_present = None, False
    if completions:
        index, function = completions[-1]
        args = json.loads(function["arguments"])
        answer, answer_present = args.get("answer"), "answer" in args
        turn = next(x for x in t["turns"] if x["assistant_index"] == index)
        schema = next(
            s for s in turn["tools"] if s["function"]["name"] == function["name"]
        )
        assert (
            "if and only if"
            in schema["function"]["parameters"]["properties"]["answer"]["description"]
        )
    failures = e["official"]["failures"]
    answer_failures = [
        f for f in failures if f["requirement"].strip() == "assert answers match."
    ]
    # Metadata classification ONLY. Never copy these traces into Actor contexts.
    expects_absent = any(
        re.search(r"==\s*'null'\s*$", f["trace"]) for f in answer_failures
    )
    categories = []
    if answer_failures:
        categories.append(
            "action_answer_not_omitted"
            if expects_absent and answer_present
            else "answer_mismatch"
        )
    nonanswer = [f for f in failures if f not in answer_failures]
    if nonanswer:
        categories.append("non_answer_constraint_failure")
    finishes = Counter(
        read_json(p)["choices"][0]["finish_reason"]
        for p in (directory / "requests").glob("*.response.json")
    )
    if finishes["length"]:
        categories.append("response_token_truncation")
    initial_names = {s["function"]["name"] for s in t["initial_tools"]}
    token_apps = {
        s["function"]["name"].split("__")[0]
        for s in t["initial_tools"]
        if "access_token" in s["function"]["parameters"].get("required", [])
    }
    missing_login = sorted(
        app for app in token_apps if app + "__login" not in initial_names
    )
    if t["tool_mode"] == "api_predictor" and missing_login:
        categories.append("selected_authenticated_apis_without_login")
    seen, counts, pages, names, multi = recommendation_counts(t["messages"])
    aggregation = None
    if pages:
        selected = [key for key, name in names.items() if name == answer]
        aggregation = {
            "page_sizes": pages,
            "unique_songs": len(seen),
            "songs_with_multiple_artists": multi,
            "total_distinct_song_artist_pairs": sum(counts.values()),
            "max_observed_artist_frequency": max(counts.values(), default=0),
            "submitted_answer_observed_frequency": max(
                (counts[k] for k in selected), default=None
            ),
            "all_artist_entries_counted": True,
        }
    return {
        "task_id": t["task_id"],
        "trajectory_id": t["trajectory_id"],
        "official_success": e["success"],
        "passed_assertions": len(e["official"]["passes"]),
        "failed_assertions": len(failures),
        "non_answer_failed_assertions": len(nonanswer),
        "final_answer_key_present": answer_present,
        "answer_expected_absent_control_only": expects_absent,
        "failure_categories": categories,
        "missing_selected_login_apps": missing_login,
        "finish_reasons": dict(finishes),
        "public_tool_errors": sum(
            "Execution failed" in m.get("content", "")
            for m in t["messages"]
            if m["role"] == "tool"
        ),
        "public_aggregation_recount": aggregation,
        "usage": t["usage"],
        "evaluation_sha256": sha256(directory / "evaluation.json"),
        "trajectory_sha256": sha256(directory / "trajectory.json"),
    }


def audit(previous, retry):
    from appworld.task import Task

    previous, retry = Path(previous), Path(retry)
    config = read_json(retry / "config.json")
    plan = load_plan(config)
    before = {
        read_json(p)["task_id"]: p.parent
        for p in previous.glob("teacher_*/trajectory.json")
    }
    after = {
        read_json(p)["task_id"]: p.parent
        for p in retry.glob("teacher_*/trajectory.json")
    }
    if set(after) != set(plan["tasks"]):
        raise ValueError("Retry is incomplete or has unexpected tasks")
    predictor, rows = official_predictor(), []
    for task in plan["tasks"]:
        old, new = (
            read_json(before[task] / "trajectory.json"),
            read_json(after[task] / "trajectory.json"),
        )
        assert old["reward"] == 0 and old["status"] == new["status"] == "valid"
        assert old["initial_state_hash"] == new["initial_state_hash"]
        assert old["observed_backend"] == new["observed_backend"]
        assert old["teacher"] == new["teacher"]
        a = read_json(retry / (after[task].name + ".config.json"))
        b = read_json(previous / (before[task].name + ".config.json"))
        for key in [
            "remaining_requests",
            "remaining_generated_tokens",
            "remaining_cost",
            "environment_seed",
        ]:
            assert a[key] == b[key]
        for key in [
            "episode_generated_tokens",
            "episode_wall_seconds",
            "max_model_turns",
            "max_tool_calls",
        ]:
            assert a["limits"][key] == b["limits"][key]
        task_object = Task.load(task, load_ground_truth=False)
        assert task_object.ground_truth is None
        selector = read_json(after[task] / "requests/000.request.json")
        assert selector["messages"] == predictor_messages(
            predictor, task_object, a["predictor_prompt_variant"]
        )
        # Full public task and public selector input are reconstructed, not just
        # scanned for a handful of forbidden words.
        assert new["messages"][:2] == [
            {"role": "system", "content": actor_system(a)},
            old["messages"][1],
        ]
        assert all(turn["tools"] == new["initial_tools"] for turn in new["turns"])
        rows.append(
            {
                "task_id": task,
                "before_b": diagnose(before[task]),
                "retry_a": diagnose(after[task]),
                "same_original_state": True,
                "same_teacher_and_per_task_budget": True,
                "public_predictor_input_reconstructed": True,
                "actor_has_no_prior_failure_feedback": True,
                "a_schema_fixed": True,
            }
        )

    def costs(key):
        fields = [
            "provider_requests",
            "model_turns",
            "tool_calls",
            "prompt_tokens",
            "generated_tokens",
            "cached_prompt_tokens",
            "usage_priced_cost",
            "wall_seconds",
        ]
        return {f: sum(row[key]["usage"][f] for row in rows) for f in fields}

    return {
        "status": "PASS",
        "scope": "A once on prior B failures; conditional diagnostic, not an unbiased A/B comparison or checkpoint recovery",
        "tasks": len(rows),
        "scenarios": plan["num_scenarios"],
        "a_official_successes": sum(r["retry_a"]["official_success"] for r in rows),
        "b_failure_subset_costs": costs("before_b"),
        "a_all_costs": costs("retry_a"),
        "rows": rows,
        "selection_plan_hash": digest(plan),
        "training_started": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--retry", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit(args.previous, args.retry)
    write_json(args.output, result)
    print(
        f"{result['a_official_successes']}/{result['tasks']} A successes; frozen public inputs and paired initial states checked"
    )


if __name__ == "__main__":
    main()
