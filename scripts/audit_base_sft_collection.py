"""Audit every coverage-selected attempt; public report omits private task content."""

import argparse
from collections import Counter
import json
import copy
from pathlib import Path

from recoveragent.answer_guard import validate_guard_record
from recoveragent.adapter import call_code
from recoveragent.demonstrations import sha256, verify_dataset, validate_receipts
from recoveragent.formal_dataset import visible_schema_replay
from recoveragent.execution_v5 import actor_system
from recoveragent.io import digest, read_json, write_json, canonical
from recoveragent.p3a import interrupted_usage
from recoveragent.protocol_lock import verify_lock
from recoveragent.sampling import load_plan, official_pool
from recoveragent.teacher_history import validate_history
from scripts.analyze_failure_retry import check_receipts_including_rejected_tail


def validate_batch(message, request, max_tools, already_called):
    """Replay frozen pre-dispatch validation only; never execute AppWorld tools."""
    calls = message.get("tool_calls", [])
    if not calls:
        return
    validate_history(
        request["messages"]
        + [message]
        + [{"role": "tool", "tool_call_id": c["id"], "content": ""} for c in calls]
    )
    if max_tools is not None and len(calls) + already_called > max_tools:
        raise ValueError("Entire batch exceeds remaining tool budget")
    for offset, call in enumerate(calls):
        f = call["function"]
        call_code(f["name"], f["arguments"], request["tools"])
        if f["name"] == "supervisor__complete_task" and offset != len(calls) - 1:
            raise ValueError("Completion must be the last batch call")


def audit(run):
    from appworld.task import Task

    run = Path(run)
    config = read_json(run / "config.json")
    plan = load_plan(config)
    if (
        plan != read_json(run / "task_manifest.json")
        or plan["hidden_labels_or_results_used"] is not True
        or plan["hidden_answers_used"] is not False
        or config["tool_mode"] != "dynamic_discovery"
        or config["compare_tool_modes"] is not False
    ):
        raise ValueError("Expected predeclared outcome-conditioned B collection")
    if digest(read_json(run / "protocol_lock.json")) != verify_lock(config):
        raise ValueError("Frozen source changed")
    manifest = verify_dataset(
        run / "clean_dataset", set(official_pool(config["appworld_root"]))
    )
    accepted = {
        a["trajectory_id"]: a for a in read_json(run / "clean_dataset/attempts.json")
    }
    rows, seen = [], set()
    planned = {r["task_id"]: r for r in plan["rows"]}
    for path in sorted(run.glob("teacher_*/trajectory.json")):
        t = read_json(path)
        directory = path.parent
        if t["task_id"] in seen or t["task_id"] not in plan["tasks"]:
            raise ValueError("Repeated or unplanned attempt")
        seen.add(t["task_id"])
        worker = read_json(run / (directory.name + ".config.json"))
        actual = interrupted_usage(directory, config["teacher"])
        for field, value in actual.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if abs(value - t["usage"].get(field, 0)) > 1e-8:
                    raise ValueError("Unreconciled raw cost field: " + field)
        if t["status"] == "valid":
            check_receipts_including_rejected_tail(
                t,
                directory,
                lambda m, r: validate_batch(
                    m, r, config["limits"]["max_tool_calls"], t["usage"]["tool_calls"]
                ),
            )
            validate_guard_record(
                dict(t, turns=[x for x in t["turns"] if "format_error" not in x])
            )
        requests = list((directory / "requests").glob("*.request.json"))
        responses = list((directory / "requests").glob("*.response.json"))
        missing_responses = len(requests) - len(responses)
        if t.get("error_type") == "TeacherTransportError":
            if missing_responses != 1 or len(responses) != len(t["turns"]):
                raise ValueError("Unexpected interrupted transport receipt boundary")
            prefix = copy.deepcopy(t)
            prefix["usage"]["provider_requests"] -= 1
            prefix["usage"]["unknown_cost_reservation"] = 0.0
            prefix["usage"]["unknown_generation_reservation"] = 0
            validate_receipts(prefix, directory)
        public = Task.load(t["task_id"], load_ground_truth=False)
        context = {
            "instruction": public.instruction,
            "supervisor": dict(public.supervisor),
            "datetime": str(public.datetime),
            "app_descriptions": public.app_descriptions,
        }
        catalogue = {
            s["function"]["name"]: s for s in public.api_docs.function_calling()
        }
        infrastructure_detail = None
        if t.get("error_type") == "TeacherBlocked":
            validate_receipts(t, directory)
            next_tools = visible_schema_replay(t, catalogue)
            latest_request = read_json(
                sorted((directory / "requests").glob("*.request.json"))[-1]
            )
            next_request = dict(
                latest_request,
                messages=t["messages"],
                tools=next_tools,
                max_tokens=min(
                    config["teacher"]["max_new_tokens"],
                    min(
                        config["limits"]["episode_generated_tokens"],
                        worker["remaining_generated_tokens"],
                    )
                    - t["usage"]["generated_tokens"],
                ),
            )
            payload_bytes = len(canonical(next_request).encode())
            if payload_bytes <= config["teacher"]["max_request_bytes"]:
                raise ValueError("TeacherBlocked not explained by request-size bound")
            infrastructure_detail = {
                "reason": "REQUEST_SIZE_LIMIT",
                "next_request_bytes": payload_bytes,
                "limit": config["teacher"]["max_request_bytes"],
                "complete_prefix_receipts_checked": True,
                "blocked_request_sent": False,
            }
        if t["status"] == "valid":
            visible_schema_replay(t, catalogue)
        for request in sorted((directory / "requests").glob("*.request.json")):
            p = read_json(request)
            if (
                p["messages"][0]["content"] != actor_system(worker)
                or json.loads(p["messages"][1]["content"]) != context
                or p["max_tokens"] > 4096
                or p["model"] != config["teacher"]["model"]
                or any(s != catalogue.get(s["function"]["name"]) for s in p["tools"])
            ):
                raise ValueError("Request differs from frozen public input/protocol")
        evaluation_path = directory / "evaluation.json"
        evaluation = read_json(evaluation_path) if evaluation_path.exists() else None
        record = accepted[t["trajectory_id"]]
        rows.append(
            {
                "trajectory_id": t["trajectory_id"],
                "sampling_reason": planned[t["task_id"]]["reason"],
                "new_attempt_number": planned[t["task_id"]]["new_attempts_so_far"] + 1,
                "prior_selected_successes": planned[t["task_id"]][
                    "prior_selected_successes"
                ],
                "task_id": t["task_id"],
                "scenario_id": public.generator_id,
                "observed_backend": t.get("observed_backend"),
                "status": t["status"],
                "official_success": t.get("reward") == 1,
                "accepted": record["accepted"],
                "filter_reasons": record["reasons"],
                "termination_reason": t["termination_reason"],
                "requests_without_response": missing_responses,
                "infrastructure_detail": infrastructure_detail,
                "failed_assertions": len(evaluation["official"]["failures"])
                if evaluation
                else None,
                "answer_guard": t.get("answer_guard"),
                "usage": t["usage"],
                "trajectory_sha256": sha256(path),
                "evaluation_sha256": sha256(evaluation_path) if evaluation else None,
            }
        )
    stop = None
    if seen != set(plan["tasks"]):
        stop = read_json(run / "collection_stop.json")
        if (
            stop["reason"] != "provider_http_402_insufficient_balance"
            or stop["attempted_tasks"] != plan["tasks"][: len(seen)]
            or set(stop["attempted_tasks"]) != seen
            or stop["unattempted_tasks"] != plan["tasks"][len(seen) :]
            or stop["task_manifest_hash"] != digest(plan)
            or stop["provider_diagnostic_sha256"]
            != sha256("reports/base_sft_provider_diagnostic.json")
            or read_json("reports/base_sft_provider_diagnostic.json")["http_status"]
            != 402
        ):
            raise ValueError("Unexplained missing planned attempts")
    rows.sort(key=lambda r: plan["tasks"].index(r["task_id"]))
    return {
        "status": "PASS",
        "run_id": run.name,
        "external_stop": stop,
        "sampling_scope": "outcome-conditioned coverage; not an unbiased benchmark",
        "rows": rows,
        "filter_reasons": dict(Counter(v for r in rows for v in r["filter_reasons"])),
        "dataset_counts": manifest["counts"],
        "costs": manifest["costs"],
        "task_manifest_hash": digest(plan),
        "dataset_manifest_sha256": sha256(run / "clean_dataset/manifest.json"),
        "all_raw_receipts_reconciled": True,
        "public_inputs_and_tools_reconstructed": True,
        "training_started": False,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run")
    p.add_argument("--output", required=True)
    a = p.parse_args()
    result = audit(a.run)
    write_json(a.output, result)
    print({k: result[k] for k in ["status", "run_id", "dataset_counts", "costs"]})


if __name__ == "__main__":
    main()
