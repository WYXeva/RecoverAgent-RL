"""Recompute public metrics from private A100 training and Student artifacts."""

import argparse
from collections import Counter
import json
from pathlib import Path

from continue_p3c import budget_before_dispatch, verify_episode
from summarize_p3b import aggregate, read, sha

from recoveragent.harness import public_tool_error
from recoveragent.io import digest
from recoveragent.state import inspect_snapshot


def session_metrics(directory, usage):
    events = [read(p)["event"] for p in sorted((directory / "turns").glob("*.json"))]
    requests = [
        read(p) for p in sorted((directory / "requests").glob("*.request.json"))
    ]
    receipts = [
        read(p) for p in sorted((directory / "requests").glob("*.response.json"))
    ]
    if usage and not usage.get("unknown_generation_reservation"):
        assert len(requests) == len(receipts) == usage["model_requests"]
        assert (
            sum(len(r["input_token_ids"]) for r in requests) == usage["prompt_tokens"]
        )
        assert sum(len(r["token_ids"]) for r in receipts) == usage["generated_tokens"]
    assert all(
        len(r["input_token_ids"]) + r["max_new_tokens"] <= 32768 for r in requests
    )
    return {
        "protocol_rejection_codes": dict(
            Counter(
                e["protocol_rejection"] for e in events if "protocol_rejection" in e
            )
        ),
        "generation_seconds": sum(r.get("generation_seconds", 0) for r in receipts),
        "peak_inference_allocated_bytes": max(
            (r.get("peak_allocated_bytes", 0) for r in receipts), default=0
        ),
        "max_input_tokens": max(
            (len(r["input_token_ids"]) for r in requests), default=0
        ),
        "call_intents": len(list((directory / "call_intents").glob("*.json"))),
        "business_error_returns": sum(
            public_tool_error(r["content"])
            for e in events
            for r in e.get("tool_results", [])
        ),
        "protocol_feedback": [
            {
                "request_id": e.get("request_id"),
                "code": e["protocol_rejection"],
                "function": e.get("rejected_function"),
                "schema_error": json.loads(e["feedback"]["content"]).get(
                    "schema_error"
                ),
                "next_response_accepted": index + 1 < len(events)
                and events[index + 1].get("status") == "complete_boundary",
            }
            for index, e in enumerate(events)
            if "protocol_rejection" in e
        ],
    }


def summarize(root):
    root = Path(root)
    run = root / "checkpoints/run_001"
    pipeline = read(run / "summary.json")
    assert pipeline["status"] == "PASS"
    stages = {}
    for stage in ("short_smoke", "length_smoke", "formal"):
        report = read(run / (stage + ".json"))
        assert (
            report["parameter_update_and_reload_pass"] and report["checkpoint_reloaded"]
        )
        assert report["trainable_before_hash"] != report["trainable_after_hash"]
        assert report["trainable_after_hash"] == report["restored_trainable_hash"]
        for name, expected in report["files"].items():
            assert not Path(name).is_absolute() and ".." not in Path(name).parts
            assert sha(run / stage / name) == expected, name
        stages[stage] = {
            k: v for k, v in report.items() if k not in {"files", "gradient_checks"}
        }
        stages[stage].update(
            optimizer_steps=len(report["gradient_checks"]),
            checkpoint_files_verified=len(report["files"]),
            source_report_sha256=sha(run / (stage + ".json")),
        )
    arms, starts = {}, {}
    for arm in ("raw_base", "base_sft"):
        directory = root / "evaluations" / arm
        summary = read(directory / "summary.json")
        rows, starts[arm] = [], {}
        for original in summary["rows"]:
            row = {
                k: original[k]
                for k in (
                    "task_id",
                    "scenario_id",
                    "partition",
                    "status",
                    "official_success",
                    "usage",
                    "termination",
                    "initial_state_hash",
                    "wall_seconds",
                    "error_type",
                    "evaluation_exception_types",
                )
                if k in original
            }
            folder = directory / (arm + "_" + row["partition"] + "_" + row["task_id"])
            evidence = folder / "official_evaluation.json"
            if evidence.exists():
                official = read(evidence)
                assert (official["status"], official["success"]) == (
                    row["status"],
                    row["official_success"],
                )
                row["official_evidence_sha256"] = sha(evidence)
            row.update(session_metrics(folder / "session", row.get("usage", {})))
            first = folder / "session/requests/001.request.json"
            if first.exists():
                starts[arm][row["task_id"]] = (
                    original["initial_state_hash"],
                    read(first)["input_token_ids"],
                )
            rows.append(row)
        arms[arm] = {
            "rows": rows,
            "overall": aggregate(rows),
            "partitions": {
                p: aggregate([r for r in rows if r["partition"] == p])
                for p in ("train", "internal_validation", "independent_dev")
            },
            "unattempted": summary["unattempted"],
        }
    paired = {
        task: starts["raw_base"][task] == starts["base_sft"][task]
        for task in starts["raw_base"].keys() & starts["base_sft"].keys()
    }
    assert all(paired.values()), "Different paired initial state or model input"
    return {
        "status": "EVIDENCE_VERIFIED",
        "training": stages,
        "pipeline": pipeline,
        "paired_evaluation": arms,
        "paired_same_initial_state_and_input": paired,
        "p3c": summarize_p3c(root),
        "teacher_requests": 0,
        "billed_gpu_cost": None,
        "recovery_training_started": False,
    }


def summarize_p3c(root):
    collections, cases, restorations = [], [], []
    for name in ("p3c_001", "p3c_002"):
        path = root / "runs" / name / "summary.json"
        if not path.exists():
            continue
        collected = read(path)
        if collections:
            assert collected["previous_summary_sha256"] == collections[0]["sha256"]
            assert collected["policy"] == collections[0]["summary"]["policy"]
            assert collected["config_sha256"] == sha(
                root / "reports/student_validation/p3c_config.json"
            )
        collections.append({"name": name, "sha256": sha(path), "summary": collected})
        cases.extend((name, case) for case in collected["outcomes"])
        restorations.extend(collected["independent_restorations"])
    planned = read(root / "worker_source/configs/p3c_train_plan_20261025.json")["tasks"]
    attempted = [case["task_id"] for _, case in cases]
    assert attempted == planned[: len(attempted)]
    rows, candidate_ids, core_ids, roles = [], set(), set(), Counter()
    for collection_name, case in cases:
        folder = root / "runs" / collection_name / case["task_id"]
        result = verify_episode(folder)
        if result["natural_failure"]:
            assert result["evaluation"]["status"] == "valid"
            assert result["evaluation"]["success"] is False and not result["fault"]
        row = {
            k: result[k]
            for k in (
                "task_id",
                "scenario_id",
                "exposure",
                "outcome",
                "natural_failure",
                "termination",
                "usage",
                "wall_seconds",
            )
        }
        row["collection"] = collection_name
        row["controller_category"] = (
            "budget_interruption_before_dispatch"
            if budget_before_dispatch(folder, result)
            else result["outcome"]
        )
        row["official_evaluation_status"] = result["evaluation"]["status"]
        row["official_success"] = result["evaluation"].get("success")
        row["checkpoint_count"] = len(result["boundaries"])
        row["candidate_roles"] = sorted(result["recovery_candidates"])
        post_error = result["recovery_candidates"].get("post_error")
        row["post_error_witness_confirmed"] = None
        if post_error:
            matching = [
                turn["event"]
                for path in (folder / "interaction/turns").glob("*.json")
                for turn in [read(path)]
                if digest(turn["messages"]) == post_error["history_hash"]
            ]
            row["post_error_witness_confirmed"] = len(matching) == 1 and any(
                public_tool_error(item["content"])
                for item in matching[0].get("tool_results", [])
            )
        boundary_ids = set()
        for boundary in result["boundaries"]:
            source = folder / "checkpoints" / Path(boundary["snapshot_ref"]).name
            manifest, _, _ = inspect_snapshot(source)
            assert manifest["checkpoint_id"] == boundary["checkpoint_id"]
            assert manifest["task_id"] == row["task_id"]
            boundary_ids.add(manifest["checkpoint_id"])
        for role, candidate in result["recovery_candidates"].items():
            assert candidate["task_active"]
            assert candidate["checkpoint_id"] in boundary_ids
            candidate_ids.add(candidate["checkpoint_id"])
            roles[role] += 1
            if role in {"post_error", "late_failure"}:
                core_ids.add(candidate["checkpoint_id"])
        row["snapshot_hashes_verified"] = len(boundary_ids)
        row["result_sha256"] = sha(folder / "result.json")
        row.update(session_metrics(folder / "interaction", row["usage"]))
        rows.append(row)
    for probe in restorations:
        assert probe["status"] == "PASS" and probe["source_unchanged"]
        assert (
            probe["database_matches"]
            and probe["history_matches"]
            and probe["tools_match"]
        )
        assert probe["runtime_matches"] and probe["agent_runtime_integrity_verified"]
    restored_ids = {p["checkpoint_id"] for p in restorations}
    if collections[-1]["summary"]["status"] == "PASS":
        assert candidate_ids == restored_ids
    usage = Counter()
    for row in rows:
        usage.update(row["usage"])
    return {
        "status": "PASS"
        if attempted == planned
        and candidate_ids == restored_ids
        and core_ids
        and collections[-1]["summary"]["status"] == "PASS"
        else "PARTIAL",
        "collections": collections,
        "rows": rows,
        "attempted_tasks": len(rows),
        "unattempted": planned[len(attempted) :],
        "natural_failures": sum(r["natural_failure"] for r in rows),
        "successes": sum(r["outcome"] == "success" for r in rows),
        "controller_categories": dict(Counter(r["controller_category"] for r in rows)),
        "checkpoints": sum(r["checkpoint_count"] for r in rows),
        "independent_restorations": restorations,
        "usage": dict(usage),
        "unique_candidate_checkpoints": len(candidate_ids),
        "unique_post_error_or_late_failure_checkpoints": len(core_ids),
        "candidate_role_counts": dict(roles),
        "unconfirmed_post_error_labels": sum(
            row["post_error_witness_confirmed"] is False for row in rows
        ),
        "all_selected_candidates_restored": candidate_ids == restored_ids,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    with Path(a.output).open("x") as f:
        json.dump(summarize(a.input), f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
