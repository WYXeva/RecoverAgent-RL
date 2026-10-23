"""Recompute public P3-B metrics from private returned artifacts; never expose text."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def aggregate(rows):
    valid = [r for r in rows if r["status"] == "valid"]
    successes = sum(r["official_success"] is True for r in valid)
    requests = sum(r.get("usage", {}).get("model_requests", 0) for r in rows)
    codes = Counter()
    for row in rows:
        codes.update(row.get("protocol_rejection_codes", {}))
    strict_errors = codes["malformed_output"] + codes["truncated_output"]
    return {
        "attempts": len(rows),
        "valid_official_evaluations": len(valid),
        "official_successes": successes,
        "unknown_outcomes": len(rows) - len(valid),
        "success_rate_over_valid": successes / len(valid) if valid else None,
        "confirmed_success_fraction_over_attempts": successes / len(rows)
        if rows
        else None,
        "protocol_rejection_codes": dict(sorted(codes.items())),
        "strict_format_error_responses": strict_errors,
        "strict_format_error_rate": strict_errors / requests if requests else None,
        "schema_argument_error_responses": codes["invalid_arguments"],
        "visibility_error_responses": codes["invisible_tool"],
        "usage": {
            k: sum(r.get("usage", {}).get(k, 0) for r in rows)
            for k in [
                "model_requests",
                "prompt_tokens",
                "generated_tokens",
                "tool_calls",
                "protocol_rejections",
                "harness_rejections",
                "unknown_generation_reservation",
            ]
        },
    }


def summarize(root):
    root = Path(root)
    run = root / "checkpoints/run_003"
    pipeline = read(run / "summary.json")
    if pipeline["status"] != "PASS":
        raise ValueError("Formal pipeline has not passed")
    stages = {}
    for name in ["short_smoke", "length_smoke", "formal"]:
        report = read(run / (name + ".json"))
        assert report["status"] == "PASS" and report["checkpoint_reloaded"]
        assert (
            report["trainable_before_hash"]
            != report["trainable_after_hash"]
            == report["restored_trainable_hash"]
        )
        for file, expected in report["files"].items():
            if Path(file).is_absolute() or ".." in Path(file).parts:
                raise ValueError("Invalid checkpoint manifest path")
            assert sha(run / name / file) == expected, file
        stages[name] = {
            k: v for k, v in report.items() if k not in ["files", "gradient_checks"]
        }
        stages[name].update(
            optimizer_steps=len(report["gradient_checks"]),
            checkpoint_files_verified=len(report["files"]),
            source_report_sha256=sha(run / (name + ".json")),
        )
    failed_steps = max(
        read(p)["global_step"]
        for p in (root / "checkpoints/run_002/short_smoke").glob(
            "checkpoint-*/trainer_state.json"
        )
    )
    assert read(root / "evaluations_v2/raw_base/config.json") == read(
        root / "evaluations_v2/base_sft/config.json"
    )
    arms = {}
    for arm in ["raw_base", "base_sft"]:
        directory = root / "evaluations_v2" / arm
        summary = read(directory / "summary.json")
        rows = []
        for original in summary["rows"]:
            row = {
                k: original[k]
                for k in [
                    "task_id",
                    "scenario_id",
                    "partition",
                    "split",
                    "arm",
                    "status",
                    "official_success",
                    "initial_state_hash",
                    "termination",
                    "usage",
                    "wall_seconds",
                    "policy",
                    "evaluation_exception_types",
                    "error_type",
                    "usage_unknown",
                ]
                if k in original
            }
            folder = directory / (arm + "_" + row["partition"] + "_" + row["task_id"])
            if original["status"] in ["valid", "evaluation_error"]:
                official = read(folder / "official_evaluation.json")
                assert (
                    official["status"] == row["status"]
                    and official["success"] == row["official_success"]
                )
                row["official_evidence_sha256"] = sha(
                    folder / "official_evaluation.json"
                )
            events = [
                read(p)["event"]
                for p in sorted((folder / "session/turns").glob("*.json"))
            ]
            row["protocol_rejection_codes"] = dict(
                Counter(
                    e["protocol_rejection"] for e in events if "protocol_rejection" in e
                )
            )
            requests = [
                read(p)
                for p in sorted((folder / "session/requests").glob("*.request.json"))
            ]
            receipts = [
                read(p)
                for p in sorted((folder / "session/requests").glob("*.response.json"))
            ]
            if not row.get("usage", {}).get("unknown_generation_reservation", 1):
                assert len(requests) == len(receipts) == row["usage"]["model_requests"]
                assert (
                    sum(len(r["input_token_ids"]) for r in requests)
                    == row["usage"]["prompt_tokens"]
                )
                assert (
                    sum(len(r["token_ids"]) for r in receipts)
                    == row["usage"]["generated_tokens"]
                )
            row["strict_format_error_responses"] = sum(
                row["protocol_rejection_codes"].get(code, 0)
                for code in ["malformed_output", "truncated_output"]
            )
            row["strict_format_error_rate"] = (
                row["strict_format_error_responses"] / len(requests)
                if requests
                else None
            )
            row["max_input_tokens"] = max(
                (len(r["input_token_ids"]) for r in requests), default=0
            )
            row["generation_seconds"] = sum(
                r.get("generation_seconds", 0) for r in receipts
            )
            row["peak_inference_allocated_bytes"] = max(
                (r.get("peak_allocated_bytes", 0) for r in receipts), default=0
            )
            rows.append(row)
        arms[arm] = {
            "rows": rows,
            "overall": aggregate(rows),
            "partitions": {
                p: aggregate([r for r in rows if r["partition"] == p])
                for p in ["train", "internal_validation", "independent_dev"]
            },
            "unattempted": summary["unattempted"],
        }
    raw = {r["task_id"]: r for r in arms["raw_base"]["rows"]}
    sft = {r["task_id"]: r for r in arms["base_sft"]["rows"]}
    pairs = {
        task: raw[task].get("initial_state_hash") == sft[task].get("initial_state_hash")
        for task in sorted(raw.keys() & sft.keys())
        if raw[task].get("initial_state_hash") and sft[task].get("initial_state_hash")
    }
    assert all(pairs.values()), "Paired initial AppWorld states differ"
    first_inputs = {}
    for task in pairs:
        inputs = []
        for arm, rows in [("raw_base", raw), ("base_sft", sft)]:
            folder = (
                root
                / "evaluations_v2"
                / arm
                / (arm + "_" + rows[task]["partition"] + "_" + task)
            )
            first = sorted((folder / "session/requests").glob("*.request.json"))[0]
            inputs.append(read(first)["input_token_ids"])
        first_inputs[task] = inputs[0] == inputs[1]
    assert all(first_inputs.values()), "Paired first model inputs differ"
    diagnostics = []
    for arm in ["raw_base", "base_sft"]:
        directory = root / "evaluations" / arm
        for original in read(directory / "summary.json")["rows"]:
            folder = directory / (
                arm + "_" + original["partition"] + "_" + original["task_id"]
            )
            requests = {
                p.stem.split(".")[0]: read(p)
                for p in (folder / "session/requests").glob("*.request.json")
            }
            receipts = {
                p.stem.split(".")[0]: read(p)
                for p in (folder / "session/requests").glob("*.response.json")
            }
            events = [
                read(p)["event"] for p in (folder / "session/turns").glob("*.json")
            ]
            diagnostics.append(
                {
                    "arm": arm,
                    "task_id": original["task_id"],
                    "status": original["status"],
                    "official_success": None,
                    "reason": "freezegun_transformers_cleanup_error_before_official_evaluation",
                    "usage_recomputed_from_actual_receipts": {
                        "model_requests": len(requests),
                        "prompt_tokens": sum(
                            len(r["input_token_ids"]) for r in requests.values()
                        ),
                        "generated_tokens": sum(
                            len(r["token_ids"]) for r in receipts.values()
                        ),
                        "tool_calls": len(
                            list((folder / "session/call_intents").glob("*.json"))
                        ),
                        "unknown_generation_reservation": sum(
                            requests[k]["max_new_tokens"]
                            for k in requests.keys() - receipts.keys()
                        ),
                    },
                    "protocol_rejections": sum(
                        "protocol_rejection" in e for e in events
                    ),
                    "source_worker_error_sha256": sha(
                        directory / (folder.name + ".worker_error.json")
                    ),
                }
            )
    all_costs = {
        k: sum(a["overall"]["usage"][k] for a in arms.values())
        + sum(d["usage_recomputed_from_actual_receipts"][k] for d in diagnostics)
        for k in [
            "model_requests",
            "prompt_tokens",
            "generated_tokens",
            "tool_calls",
            "unknown_generation_reservation",
        ]
    }
    return {
        "scope": "fixed_6_task_p3b_acceptance_not_full_benchmark",
        "formal_manifest_sha256": pipeline["manifest_sha256"],
        "training": stages,
        "failed_smoke_optimizer_steps": failed_steps,
        "all_actual_optimizer_steps": failed_steps
        + sum(s["optimizer_steps"] for s in stages.values()),
        "evaluation": arms,
        "initial_infrastructure_attempts": diagnostics,
        "all_evaluation_attempts": len(diagnostics)
        + sum(a["overall"]["attempts"] for a in arms.values()),
        "all_evaluation_usage_including_failed_workers": all_costs,
        "paired_initial_state_equal": pairs,
        "paired_first_input_tokens_equal": first_inputs,
        "unknown_gpu_billed_cost": True,
        "teacher_requests": 0,
        "recovery_sft_or_grpo_started": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    a = parser.parse_args()
    result = summarize(a.input)
    Path(a.output).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
