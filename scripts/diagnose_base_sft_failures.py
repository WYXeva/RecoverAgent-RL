"""Recompute failure metadata from immutable receipts, without model calls or answers."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def observed_events(t):
    calls = {}
    for message in t["messages"]:
        if message["role"] == "assistant":
            for call in message.get("tool_calls", []):
                calls[call["id"]] = call["function"]
        elif message["role"] == "tool":
            function = calls[message["tool_call_id"]]
            arguments = function["arguments"]
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            try:
                output = json.loads(message["content"])
            except ValueError:
                output = None
            yield function["name"], arguments, output, message["content"]


def public_checks(t):
    events = list(observed_events(t))
    issued, changed_tokens, auth_errors, empty_pages = {}, 0, 0, []
    for name, args, output, text in events:
        app = name.split("__")[0]
        token = args.get("access_token")
        if token and token not in issued.get(app, set()):
            changed_tokens += 1
        auth_errors += "status code is 401" in text
        if name.endswith("__login") and isinstance(output, dict):
            if output.get("access_token"):
                issued.setdefault(app, set()).add(output["access_token"])
        if isinstance(output, list) and not output and "page_index" in args:
            empty_pages.append({"tool": name, "page_index": args["page_index"]})
    checks = {
        "calls_with_token_not_previously_returned_by_same_app_login": changed_tokens,
        "tool_401_responses": auth_errors,
        "empty_pages": empty_pages,
        "added_friend_calls": sum(n == "venmo__add_friend" for n, *_ in events),
    }
    # Observed recommendation results only; no hidden answers are loaded.
    recommendation_rows = {
        row["song_id"]: row
        for name, _, output, _ in events
        if name == "spotify__show_recommendations" and isinstance(output, list)
        for row in output
    }
    if recommendation_rows:
        counts, names = Counter(), {}
        for song in recommendation_rows.values():
            counts.update({artist["id"] for artist in song["artists"]})
            names.update({artist["id"]: artist["name"] for artist in song["artists"]})
        answers = [
            a.get("answer") for n, a, _, _ in events if n == "supervisor__complete_task"
        ]
        if answers and counts:
            leaders = {names[k] for k, v in counts.items() if v == max(counts.values())}
            checks["recommendation_tally"] = {
                "unique_songs": len(recommendation_rows),
                "artist_song_pairs": sum(counts.values()),
                "max_count": max(counts.values()),
                "answer_matches_observed_max": answers[-1] in leaders,
            }
    return checks


def diagnose(root, metrics_path, docs_root):
    old = read(metrics_path)
    expected = {
        r["trajectory_id"]: r
        for batch in old["all_attempt_audits"]
        for r in batch["rows"]
    }
    rows, counts, hashes, catalogs = [], Counter(), {}, {}
    for path in sorted(root.glob("base_sft_v1_20261021_b*/teacher_*/trajectory.json")):
        t = read(path)
        previous = expected[t["trajectory_id"]]
        if sha(path) != previous["trajectory_sha256"]:
            raise ValueError("Source trajectory changed: " + str(path))
        evaluation = path.parent / "evaluation.json"
        if evaluation.exists() and sha(evaluation) != previous["evaluation_sha256"]:
            raise ValueError("Source evaluation changed: " + str(evaluation))
        if t["split"] != "train" or t["tool_mode"] != "dynamic_discovery":
            raise ValueError("Unexpected collection scope")
        detail = {}
        if t.get("reward") == 1:
            category = "official_success"
        elif t["status"] == "infrastructure_error":
            category = t["error_type"]
            detail["requests_without_response"] = previous["requests_without_response"]
            detail["request_size"] = previous["infrastructure_detail"]
        elif t["termination_reason"] == "invalid_completion":
            turn = t["turns"][-1]
            request = read(
                path.parent / "requests" / (turn["request_id"] + ".request.json")
            )
            response = read(
                path.parent / "requests" / (turn["request_id"] + ".response.json")
            )
            choice = response["choices"][0]
            calls = choice["message"].get("tool_calls", [])
            visible = {s["function"]["name"] for s in request["tools"]}
            unavailable = [
                c["function"]["name"]
                for c in calls
                if c["function"]["name"] not in visible
            ]
            if choice["finish_reason"] == "length":
                category = "output_truncated"
            elif unavailable:
                category = "unavailable_tool"
            else:
                raise ValueError("Unclassified format error")
            exists = []
            for name in unavailable:
                app, api = name.split("__")
                if app not in catalogs:
                    catalogs[app] = read(docs_root / (app + ".json"))
                exists.append(api in catalogs[app])
            detail.update(
                finish_reason=choice["finish_reason"],
                max_tokens=request["max_tokens"],
                response_tokens=turn["usage"]["generated_tokens"],
                requested_batch_calls=len(calls),
                rejected_batch_dispatched=turn.get("dispatched_count", 0),
                unavailable_tools=unavailable,
                unavailable_tools_exist_in_public_catalogue=exists,
            )
        elif t["termination_reason"] == "budget_exhausted":
            category = "episode_budget_exhausted"
        elif t["termination_reason"] == "completion_claim":
            category = "completion_official_failure"
        else:
            raise ValueError("Unclassified termination")
        checks = public_checks(t)
        counts[category] += 1
        snapshot = path.parent / "terminal/dbs/model_hashes.json"
        if snapshot.exists():
            hashes[t["trajectory_id"]] = {
                "sha256": sha(snapshot),
                "empty": read(snapshot) == {},
            }
        if category != "official_success":
            rows.append(
                {
                    "trajectory_id": t["trajectory_id"],
                    "task_id": t["task_id"],
                    "batch": path.parts[-3],
                    "source": str(path),
                    "source_sha256": sha(path),
                    "evaluation_sha256": sha(evaluation)
                    if evaluation.exists()
                    else None,
                    "category": category,
                    "termination": t["termination_reason"],
                    "requests": t["usage"]["provider_requests"],
                    "tools": t["usage"]["tool_calls"],
                    "generated_tokens": t["usage"]["generated_tokens"],
                    "detail": detail,
                    "public_history_checks": checks,
                }
            )
        elif checks["calls_with_token_not_previously_returned_by_same_app_login"]:
            raise ValueError("Unexpected copied-token anomaly in successful source")
    if sum(counts.values()) != len(expected):
        raise ValueError("Missing or repeated attempt")
    return {
        "scope": "all 118 B attempts; retrospective diagnosis, not new performance evaluation",
        "reference_metrics_sha256": sha(metrics_path),
        "counts": dict(counts),
        "failures": rows,
        "original_receipts_and_evaluations_match_prior_hashes": True,
        "terminal_hash_files": hashes,
        "empty_terminal_hash_files": sum(r["empty"] for r in hashes.values()),
        "model_requests": 0,
        "real_tool_calls": 0,
        "hidden_answers_loaded": False,
        "training_started": False,
        "original_rewards_modified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("runs"))
    parser.add_argument("--metrics", default="reports/base_sft_dataset_v1_metrics.json")
    parser.add_argument(
        "--docs-root", type=Path, default=Path("data/appworld/data/api_docs/standard")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = diagnose(args.root, args.metrics, args.docs_root)
    with args.output.open("x") as out:
        json.dump(result, out, indent=2)
        out.write("\n")
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
