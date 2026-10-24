"""Read-only P3-B receipt/target audit; emit hashes and counts, never task text."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from recoveragent.adapter import call_code
from recoveragent.base_sft import load_tokenizer
from recoveragent.student import parse_batch


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def calls(message):
    return [
        (c["function"]["name"], json.loads(c["function"]["arguments"]))
        for c in message.get("tool_calls", [])
    ]


def audit_dataset(dataset, tokenizer):
    result = {}
    for split in ["train", "validation"]:
        source = Path(dataset) / "model_input" / (split + ".jsonl")
        records = [json.loads(line) for line in source.read_text().splitlines()]
        targets = {}
        discovery_count = 0
        for record in records:
            for turn in record["turns"]:
                if turn.get("supervise") is False:
                    continue
                message = record["messages"][turn["assistant_index"]]
                targets[(record["trajectory_id"], turn["assistant_index"])] = (
                    message,
                    turn["tools"],
                )
                for name, args in calls(message):
                    call_code(name, args, turn["tools"])
                    if name == "api_docs__show_api_descriptions":
                        discovery_count += 1
                        assert args.get("app_name"), "Missing training app_name"
        checked, supervised = 0, 0
        with (Path(dataset) / "tokenized" / (split + ".jsonl")).open() as stream:
            for line in stream:
                sample = json.loads(line)
                message, tools = targets.pop(
                    (sample["trajectory_id"], sample["assistant_index"])
                )
                start = sample["target_start"]
                assert sample["labels"][:start] == [-100] * start
                assert sample["labels"][start:] == sample["input_ids"][start:]
                assert sample["loss_mask"] == [int(v != -100) for v in sample["labels"]]
                decoded = tokenizer.decode(
                    sample["input_ids"][start:], skip_special_tokens=False
                )
                parsed = parse_batch(decoded, "audit", "eos")
                assert calls(parsed) == calls(message)
                for name, args in calls(parsed):
                    call_code(name, args, tools)
                checked += 1
                supervised += len(sample["input_ids"]) - start
        assert not targets
        result[split] = {
            "trajectories": len(records),
            "assistant_targets_checked": checked,
            "supervised_tokens": supervised,
            "show_api_descriptions_calls": discovery_count,
            "missing_app_name_in_training_targets": 0,
            "current_harness_prompt_trajectories": sum(
                "PUBLIC HARNESS PROTOCOL v1" in r["messages"][0]["content"]
                for r in records
            ),
            "prompt_versions": dict(
                Counter(r["actor_prompt_variant"] for r in records)
            ),
            "tool_modes": dict(Counter(r["tool_mode"] for r in records)),
            "model_input_sha256": sha(source),
        }
    return result


def audit_episodes(root, tokenizer):
    results = {}
    for arm in ["raw_base", "base_sft"]:
        rows = []
        for ep in sorted((Path(root) / "evaluations_v2" / arm).iterdir()):
            if not ep.is_dir():
                continue
            original = read(ep / "result.json")
            official = read(ep / "official_evaluation.json")
            tracker = official["official"]
            complete = (
                len(tracker["passes"]) + len(tracker["failures"])
                == tracker["num_tests"]
            )
            assert complete
            errors, executed = [], Counter()
            missing_app_requests = []
            observed_song_dates, removed_song_ids = {}, []
            for turn in sorted((ep / "session/turns").glob("*.json")):
                event = read(turn)["event"]
                request = read(
                    ep / "session/requests" / (event["request_id"] + ".request.json")
                )
                response = read(
                    ep / "session/requests" / (event["request_id"] + ".response.json")
                )
                text = tokenizer.decode(
                    response["token_ids"], skip_special_tokens=False
                )
                try:
                    batch = parse_batch(text, "audit", response["finish_reason"])
                except ValueError:
                    assert event["protocol_rejection"] in [
                        "truncated_output",
                        "malformed_output",
                    ]
                    batch = {"role": "assistant", "content": ""}
                visible = {
                    t["function"]["name"]: t["function"]["parameters"]
                    for t in request["tools"]
                }
                missing = []
                for name, args in calls(batch):
                    if name in visible:
                        required = set(visible[name].get("required", []))
                        missing.extend(sorted(required - args.keys()))
                    if (
                        name == "api_docs__show_api_descriptions"
                        and "app_name" not in args
                    ):
                        missing_app_requests.append(event["request_id"])
                    if (
                        event.get("status") == "complete_boundary"
                        and name == "spotify__remove_song_from_library"
                    ):
                        removed_song_ids.append(args["song_id"])
                if event.get("protocol_rejection"):
                    errors.append(
                        {
                            "request_id": event["request_id"],
                            "code": event["protocol_rejection"],
                            "function": event.get("rejected_function"),
                            "missing_required_fields": missing,
                        }
                    )
                for response in event.get("tool_results", []):
                    executed[response["name"]] += 1
                    if response["name"] == "spotify__show_song":
                        try:
                            observed = json.loads(response["content"])
                            observed_song_dates[observed["song_id"]] = observed[
                                "release_date"
                            ]
                        except (ValueError, KeyError, TypeError):
                            pass
            row = {
                "task_id": original["task_id"],
                "partition": original["partition"],
                "project_guard_status": official["status"],
                "project_guard_success": official["success"],
                "official_tracker_success": tracker["success"],
                "official_tracker_all_tests_recorded": complete,
                "exception_types": official["exception_types"],
                "official_evidence_sha256": sha(ep / "official_evaluation.json"),
                "termination": original["termination"],
                "usage": original["usage"],
                "missing_app_name_requests": missing_app_requests,
                "protocol_errors": errors,
                "executed_function_counts": dict(executed),
                "successful_complete_without_business_tools": (
                    original["termination"] == "completion_claim"
                    and not any(
                        not n.startswith(("supervisor__", "api_docs__"))
                        for n in executed
                    )
                ),
            }
            if original["termination"] == "context_budget_exhausted":
                context = read(ep / "terminal/context.json")
                rendered = tokenizer.apply_chat_template(
                    context["messages"],
                    tools=context["tools"],
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
                row["next_input_tokens_from_saved_terminal"] = len(
                    tokenizer(rendered, add_special_tokens=False)["input_ids"]
                )
                row["last_input_tokens"] = len(request["input_token_ids"])
                row["last_generation_cap"] = request["max_new_tokens"]
            if removed_song_ids:
                # Public task year and actual public observations, never a gold list.
                instruction = json.loads(request["messages"][1]["content"])[
                    "instruction"
                ]
                assert "before 2021 year" in instruction
                assert all(i in observed_song_dates for i in removed_song_ids)
                row["public_date_filter_audit"] = {
                    "removed": len(removed_song_ids),
                    "observed_release_before_2021": sum(
                        observed_song_dates[i][:4] < "2021" for i in removed_song_ids
                    ),
                    "observed_release_2021_or_later": sum(
                        observed_song_dates[i][:4] >= "2021" for i in removed_song_ids
                    ),
                }
            rows.append(row)
        results[arm] = {
            "rows": rows,
            "attempts": len(rows),
            "official_tracker_successes": sum(
                r["official_tracker_success"] for r in rows
            ),
            "project_guard_unknowns": sum(
                r["project_guard_success"] is None for r in rows
            ),
            "missing_app_name_episodes": sum(
                bool(r["missing_app_name_requests"]) for r in rows
            ),
        }
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    tokenizer = load_tokenizer()
    result = {
        "scope": "offline_existing_receipt_diagnosis_not_new_rollout",
        "dataset": audit_dataset(args.dataset, tokenizer),
        "evaluation": audit_episodes(args.input, tokenizer),
        "model_requests": 0,
        "optimizer_steps": 0,
        "historical_rewards_modified": False,
        "official_validator_modified": False,
    }
    with Path(args.output).open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
