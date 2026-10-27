"""Audit a future paired EGTM evaluation from actual receipts; no model execution."""

import argparse
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
        for path in requests:
            request = read_json(path)
            if request["policy"] != config["expected_policy"]:
                raise ValueError("Mixed policies in EGTM results")
            inputs += len(request["input_token_ids"])
            compilation = request.get("memory_compilation", {})
            if config["memory"]["enabled"] != bool(compilation):
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
        for path in sorted((folder / "session/turns").glob("*.json")):
            turn = read_json(path)
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
        rows.append(
            {
                "task_id": row["task_id"],
                "partition": row["partition"],
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
                "memory_update_seconds": usage.get("memory_update_seconds", 0),
                "context_compile_seconds": usage.get("context_compile_seconds", 0),
                "memory_snapshot_bytes": sum(
                    p.stat().st_size
                    for p in (folder / "terminal/memory").glob("*")
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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    result = {
        arm: summarize(f"configs/egtm_v1_eval_{arm.lower()}.json", directory)
        for arm, directory in (("A", args.a), ("B", args.b))
    }
    result["interpretation"] = (
        "Two independent model trajectories per task. Within-B uncompressed counts are a replay estimate, not extra model requests. Repeated calls are not automatically mistakes."
    )
    write_json(args.output, result)


if __name__ == "__main__":
    main()
