"""Audit a future paired EGTM evaluation from actual receipts; no model execution."""

import argparse
import json
from collections import Counter
from pathlib import Path

from recoveragent.harness import public_tool_error
from recoveragent.io import digest, read_json, write_json


def summarize(config_path, directory):
    config, directory = read_json(config_path), Path(directory)
    saved = read_json(directory / "config.json")
    if saved != config:
        raise ValueError("Evaluation config differs from the frozen arm")
    summary = read_json(directory / "summary.json")
    expected = [c["task_id"] for c in config["cases"]]
    if [r["task_id"] for r in summary["rows"]] != expected[: len(summary["rows"])]:
        raise ValueError("Evaluation task order differs from the fixed plan")
    rows = []
    for row in summary["rows"]:
        folder = directory / f"base_sft_{row['partition']}_{row['task_id']}"
        requests = sorted((folder / "session/requests").glob("*.request.json"))
        inputs, original_inputs, responses, repetitions, business_errors = 0, 0, 0, 0, 0
        queries, emitted_ids = Counter(), set()
        memory_usage = Counter()
        first_request, l1_active = None, False
        accepted_l1 = None
        visible_l1, missing_l1, compressed = 0, 0, 0
        for path in requests:
            request = read_json(path)
            if first_request is None:
                first_request = request
            turn_path = (
                folder / "session/turns" / path.name.replace(".request.json", ".json")
            )
            event = read_json(turn_path)["event"] if turn_path.exists() else {}
            if request["policy"] != config["expected_policy"]:
                raise ValueError("Mixed policies in EGTM results")
            inputs += len(request["input_token_ids"])
            compilation = request.get("memory_compilation", {})
            internal = request.get("purpose") == "memory_update"
            if internal and event.get("memory_status") == "accepted":
                l1_active = True
                memory_usage["memory_accepted"] += 1
            if not internal:
                actual_visible = compilation.get("l1_visible", False)
                if (
                    config["memory"]["version"] == "egtm-model-working-memory-v2.1"
                    and l1_active
                ):
                    payloads = []
                    for message in request["messages"]:
                        try:
                            payload = json.loads(message.get("content", ""))
                        except (ValueError, TypeError):
                            continue
                        if (
                            isinstance(payload, dict)
                            and payload.get("kind") == "internal_model_memory_data"
                        ):
                            payloads.append(payload)
                    actual_visible = (
                        len(payloads) == 1
                        and payloads[0]["working_memory"]["l1"] == accepted_l1
                    )
                visible_l1 += int(actual_visible)
                missing_l1 += int(l1_active and not actual_visible)
                compressed += int(compilation.get("compressed", False))
            if internal:
                memory_usage["memory_update_requests"] += 1
                memory_usage["memory_update_input_tokens"] += len(
                    request["input_token_ids"]
                )
            if config["memory"]["enabled"] != bool(compilation or internal):
                raise ValueError("Memory arm differs from its requests")
            original_inputs += compilation.get(
                "original_tokens", len(request["input_token_ids"])
            )
            if len(request["input_token_ids"]) + request["max_new_tokens"] > 32768:
                raise ValueError("Invalid full context window")
            response_path = path.with_name(path.name.replace(".request.", ".response."))
            if response_path.exists():
                response = read_json(response_path)
                if (
                    response["policy"] != request["policy"]
                    or response["input_token_ids"] != request["input_token_ids"]
                ):
                    raise ValueError("Generation receipt does not match input")
                responses += len(response["token_ids"])
                if internal and event.get("memory_status") == "accepted":
                    accepted_l1 = json.loads(
                        response["text"]
                        .removesuffix("<|endoftext|>")
                        .removesuffix("<|im_end|>")
                        .strip()
                    )
                if internal:
                    memory_usage["memory_update_output_tokens"] += len(
                        response["token_ids"]
                    )
        for path in sorted((folder / "session/turns").glob("*.json")):
            turn = read_json(path)
            if turn["event"].get("memory_status") == "rejected":
                memory_usage["memory_validation_failures"] += 1
            calls = {
                c["id"]: c["function"]
                for m in turn["messages"]
                for c in m.get("tool_calls", [])
            }
            verdicts = turn["event"].get("harness_verdicts", [])
            for i, result in enumerate(turn["event"].get("tool_results", [])):
                if i < len(verdicts) and verdicts[i] is not None:
                    continue
                if result["tool_call_id"] in emitted_ids:
                    raise ValueError("Duplicate result receipt")
                emitted_ids.add(result["tool_call_id"])
                signature = digest(calls[result["tool_call_id"]])
                repetitions += int(queries[signature] > 0)
                queries[signature] += 1
                business_errors += int(public_tool_error(result["content"]))
        usage = row.get("usage", {})
        if usage and (
            inputs != usage["prompt_tokens"] or responses != usage["generated_tokens"]
        ):
            raise ValueError("Reported usage does not match actual receipts")
        if (
            config["memory"].get("version")
            in {"egtm-model-working-memory-v2", "egtm-model-working-memory-v2.1"}
            and config["memory"]["enabled"]
        ):
            if any(
                usage.get(k, 0) != memory_usage[k]
                for k in (
                    "memory_update_requests",
                    "memory_update_input_tokens",
                    "memory_update_output_tokens",
                    "memory_validation_failures",
                )
            ):
                raise ValueError("Memory usage differs from raw receipts")
        rows.append(
            {
                "task_id": row["task_id"],
                "partition": row["partition"],
                "initial_state_hash": row.get("initial_state_hash"),
                "first_input_hash": digest(first_request["input_token_ids"])
                if first_request
                else None,
                "first_messages_hash": digest(first_request["messages"])
                if first_request and "messages" in first_request
                else None,
                "first_tools_hash": digest(first_request["tools"])
                if first_request and "tools" in first_request
                else None,
                "first_request_is_business": bool(
                    first_request and not first_request.get("purpose")
                ),
                "wall_seconds": row.get("wall_seconds"),
                "protocol_rejections": row.get("protocol_rejections", 0),
                "outcome": "infrastructure_error"
                if row["status"] == "infrastructure_error"
                else "evaluation_unknown"
                if row["status"] != "valid" or type(row["official_success"]) is not bool
                else "success"
                if row["official_success"]
                else "task_failure",
                "memory_acceptance_rate": memory_usage["memory_accepted"]
                / memory_usage["memory_update_requests"]
                if memory_usage["memory_update_requests"]
                else None,
                "l1_visible_requests": visible_l1,
                "accepted_l1_missing_requests": missing_l1,
                "compressed_requests": compressed,
                "status": row["status"],
                "official_success": row["official_success"],
                "model_requests": len(requests),
                "input_tokens": inputs,
                "same_history_uncompressed_tokens": original_inputs,
                "generated_tokens": responses,
                "tool_call_intents": usage.get("tool_calls"),
                "known_tool_returns": len(emitted_ids),
                "repeat_exact_calls": repetitions,
                "business_errors": business_errors,
                "context_exhausted": row.get("termination")
                == "context_budget_exhausted",
                "partial_or_unknown_execution": row.get("termination")
                == "interrupted_batch",
                "unknown_generation_reservation": usage.get(
                    "unknown_generation_reservation"
                ),
                **dict(memory_usage),
                "saved_input_tokens": usage.get("saved_input_tokens", 0),
                "net_input_saving_same_actions": original_inputs
                - inputs
                - memory_usage["memory_update_input_tokens"],
                "memory_update_seconds": usage.get("memory_update_seconds", 0),
                "context_compile_seconds": usage.get("context_compile_seconds", 0),
                "memory_snapshot_bytes": sum(
                    p.stat().st_size
                    for p in (folder / "terminal/memory").rglob("*")
                    if p.is_file()
                ),
                "wrong_business_side_effects": None,
            }
        )
    valid = [
        r
        for r in rows
        if r["status"] == "valid" and type(r["official_success"]) is bool
    ]
    return {
        "planned": len(expected),
        "all_tasks_attempted": len(rows) == len(expected),
        "complete": bool(expected) and len(valid) == len(expected),
        "infrastructure_errors": sum(
            r["outcome"] == "infrastructure_error" for r in rows
        ),
        "evaluation_unknown": sum(r["outcome"] == "evaluation_unknown" for r in rows),
        "task_failures": sum(r["outcome"] == "task_failure" for r in rows),
        "attempted": len(rows),
        "valid_evaluations": len(valid),
        "unknown_evaluations": len(rows) - len(valid),
        "successes": sum(r["official_success"] for r in valid),
        "success_rate_valid": sum(r["official_success"] for r in valid) / len(valid)
        if valid
        else None,
        "unattempted_tasks": expected[len(rows) :],
        "rows": rows,
        "wrong_side_effects_note": "Not inferred from DB changes or business errors; requires separate control-side audit.",
    }


def paired_report(config_a, directory_a, config_b, directory_b):
    """A complete audit is separate from measured success and pilot readiness."""
    import copy

    ca, cb = read_json(config_a), read_json(config_b)
    normalized = copy.deepcopy(ca)
    normalized["memory"]["enabled"] = True
    if ca["memory"]["enabled"] or not cb["memory"]["enabled"] or normalized != cb:
        raise ValueError(
            "Paired model/tasks/protocol/budgets must match except Memory enabled"
        )
    cases = ca["cases"]
    if not cases or len({r["task_id"] for r in cases}) != len(cases):
        raise ValueError("Paired plan must contain unique nonempty tasks")
    arms = {
        "A": summarize(config_a, directory_a),
        "B": summarize(config_b, directory_b),
    }
    rows, mismatches, unverified = [], [], []
    by_arm = {
        arm: {r["task_id"]: r for r in result["rows"]} for arm, result in arms.items()
    }
    for case in cases:
        task = case["task_id"]
        a, b = by_arm["A"].get(task), by_arm["B"].get(task)
        checks = {
            key: bool(a and b and a[key] is not None and a[key] == b[key])
            for key in (
                "initial_state_hash",
                "first_input_hash",
                "first_messages_hash",
                "first_tools_hash",
            )
        }
        matched = all(checks.values()) and bool(
            a
            and b
            and a["first_request_is_business"]
            and b["first_request_is_business"]
        )
        mismatch = bool(
            a
            and b
            and any(
                a[key] is not None and b[key] is not None and a[key] != b[key]
                for key in checks
            )
        )
        if mismatch:
            mismatches.append(task)
        elif not matched:
            unverified.append(task)
        rows.append(
            {
                "task_id": task,
                "scenario_id": case["scenario_id"],
                "A": a,
                "B": b,
                "pair_checks": checks,
                "paired_initial_conditions_verified": matched,
            }
        )
    complete = all(result["complete"] for result in arms.values())
    visible = all(r["accepted_l1_missing_requests"] == 0 for r in arms["B"]["rows"])
    status = (
        "FAIL"
        if mismatches or not visible
        else "PASS"
        if complete and not unverified
        else "INCOMPLETE"
    )
    protocol_clean = all(
        not r["protocol_rejections"] and not r.get("memory_validation_failures", 0)
        for result in arms.values()
        for r in result["rows"]
    )
    pilot_ready = (
        status == "PASS"
        and protocol_clean
        and any(
            r.get("memory_accepted", 0) and r["l1_visible_requests"]
            for r in arms["B"]["rows"]
        )
    )
    return {
        "status": status,
        "stage": ca.get("experiment_stage"),
        "all_tasks_completed": complete,
        "model_policy": ca["expected_policy"],
        "runtime_revision": ca["runtime_revision"],
        "mismatched_tasks": mismatches,
        "unverified_initial_conditions": unverified,
        "pilot_protocol_ready": pilot_ready,
        "semantic_l1_review": "NOT_AUTOMATED: review constraints/completed claims/need_evidence in actual receipts",
        "A": arms["A"],
        "B": arms["B"],
        "paired_tasks": rows,
        "interpretation": "PASS certifies complete paired execution and valid evaluations, not Memory superiority. "
        "Within-B net_input_saving_same_actions is a fixed-history counterfactual including Memory input overhead. "
        "Unknown evaluations are not task failures. Formal expansion still requires semantic review and GPU authorization.",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--version", choices=["v1", "v2", "v2_improvement"], default="v1")
    p.add_argument("--stage", choices=["eval", "pilot"], default="eval")
    args = p.parse_args()
    if args.version == "v2_improvement":
        result = paired_report(
            f"configs/egtm_v2_improvement_{args.stage}_a.json",
            args.a,
            f"configs/egtm_v2_improvement_{args.stage}_b.json",
            args.b,
        )
        write_json(args.output, result)
        return
    result = {
        arm: summarize(
            f"configs/egtm_{args.version}_eval_{arm.lower()}.json", directory
        )
        for arm, directory in (("A", args.a), ("B", args.b))
    }
    result["interpretation"] = (
        "Two independent model trajectories per task. Within-B uncompressed counts are a replay estimate, not extra model requests. Repeated calls are not automatically mistakes."
    )
    write_json(args.output, result)


if __name__ == "__main__":
    main()
