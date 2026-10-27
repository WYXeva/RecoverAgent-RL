"""No-model metadata audit and serial train-only clean-state runtime probes."""

import argparse
from pathlib import Path
import subprocess
import sys
import time
import traceback

from recoveragent.io import digest, read_json, write_json
from recoveragent.unified_split import (
    audit_pool,
    base_source_inventory,
    coverage,
    exposure_audit,
    make_split,
    sha,
)


def prepare(root, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    audit = audit_pool(root)
    write_json(output / "full_task_pool_audit.json", audit)
    for name, train_only in (
        ("full_pool_candidate", False),
        ("unified_task_split", True),
    ):
        write_json(output / (name + ".json"), make_split(audit, train_only=train_only))
    split = read_json(output / "unified_task_split.json")
    exposures = exposure_audit(
        split,
        "data/base_sft_v1",
        [
            "reports/a100_sft_p3c_20261026.json",
            "reports/p3b_results_20261023.json",
            "reports/egtm_v2_improvement_pilot.json",
            "reports/egtm_harness_29k_live_results.json",
        ],
    )
    write_json(output / "stage_data_leakage_audit.json", exposures)
    write_json(
        output / "full_candidate_exposure_audit.json",
        exposure_audit(
            read_json(output / "full_pool_candidate.json"),
            "data/base_sft_v1",
            [
                s["path"]
                for s in exposures["sources"]
                if s["category"] == "student_execution_or_development_analysis"
            ],
        ),
    )
    write_json(
        output / "unified_base_sft_source_inventory.json",
        base_source_inventory(split, "data/base_sft_v1"),
    )
    write_json(output / "private_task_coverage.json", coverage(split, root))
    print(
        {
            "tasks": audit["tasks"],
            "scenarios": audit["scenarios"],
            "operational": split["counts"],
        },
        flush=True,
    )


def worker(root, task, output):
    # Imports deferred: metadata preparation does not initialize any environment.
    from recoveragent.student_evaluation import EvaluationAdapter, evaluate_terminal
    from recoveragent.evaluation import environment_check

    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    experiment = "unified_audit_" + digest(str(target.resolve()))[:24]
    adapter = None
    record = {
        "task_id": task,
        "official_split": "train",
        "status": "BLOCKED",
        "model_requests": 0,
    }
    try:
        record["environment"] = environment_check()
        adapter = EvaluationAdapter(root, task, experiment, "train")
        record["scenario_id"] = adapter.world.task.generator_id
        record["initial_state_hash"] = adapter.fingerprint()
        tools = adapter.initial_tools()
        record["visible_tools_hash"] = digest(tools)
        record["public_tools"] = len(adapter.catalogue)
        record["initial_tools"] = len(tools)
        response, _ = adapter.execute(
            "api_docs__show_api_descriptions", {"app_name": "api_docs"}, tools
        )
        if "Execution failed" in response:
            raise ValueError("Read-only public API probe failed")
        record["read_tool_response_sha256"] = digest(response)
        record["read_only_state_unchanged"] = (
            record["initial_state_hash"] == adapter.fingerprint()
        )
        if not record["read_only_state_unchanged"]:
            raise ValueError("Read probe changed database")
        adapter.save_terminal()
        adapter.close()
        adapter = None
        evaluation = evaluate_terminal(task, experiment, root, "train", target)
        record.update(
            evaluation_status=evaluation["status"],
            evaluation_category=evaluation["category"],
            official_result_complete=evaluation["official_result_complete"],
            official_evidence_sha256=sha(target / "official_evaluation.json"),
        )
        record["status"] = "PASS" if evaluation["status"] == "valid" else "BLOCKED"
        record["note"] = (
            "Clean-state evaluator availability, not model task success or task solvability proof."
        )
    except Exception as exc:
        record["error_type"] = type(exc).__name__
        (target / "exception.log").write_text(traceback.format_exc())
    finally:
        if adapter:
            adapter.close()
        record["seconds"] = time.perf_counter() - started
        write_json(target / "probe.json", record)
    print(task, record["status"], flush=True)


def probe(root, plan_path, output, previous=None):
    plan = read_json(plan_path)
    from recoveragent.unified_split import validate_split

    validate_split(plan)
    if plan["scope"] != "official_train":
        raise ValueError("No test/dev task probing for custom training split")
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    rows = []
    ordered = sorted(plan["rows"], key=lambda r: r["task_id"])
    prior = read_json(Path(previous) / "summary.json") if previous else None
    if prior:
        if prior["split_manifest_sha256"] != plan["manifest_sha256"] or [
            r["task_id"] for r in prior["rows"]
        ] != [r["task_id"] for r in ordered[: len(prior["rows"])]]:
            raise ValueError("Prior audit must be the exact same plan prefix")
        ordered = ordered[len(prior["rows"]) :]
    for row in ordered:
        task = row["task_id"]
        with (target / (task + ".log")).open("x") as log:
            try:
                subprocess.run(
                    [
                        sys.executable,
                        __file__,
                        "worker",
                        "--root",
                        root,
                        "--task",
                        task,
                        "--output",
                        str(target / task),
                    ],
                    check=True,
                    timeout=120,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                result = read_json(target / task / "probe.json")
            except (subprocess.SubprocessError, OSError) as exc:
                result = {
                    "task_id": task,
                    "status": "BLOCKED",
                    "error_type": type(exc).__name__,
                }
        rows.append(result)
        print(len(rows), task, result["status"], flush=True)
        if len(rows) >= 2 and all(
            r.get("error_type") or r.get("evaluation_category") == "environment_error"
            for r in rows[-2:]
        ):
            break
    write_json(
        target / "summary.json",
        {
            "split_manifest_sha256": plan["manifest_sha256"],
            "rows": rows,
            "planned": len(plan["rows"]),
            "model_requests": 0,
            "previous_summary": {
                "path": str(Path(previous) / "summary.json"),
                "sha256": sha(Path(previous) / "summary.json"),
            }
            if previous
            else None,
            "unattempted": sorted(
                {r["task_id"] for r in ordered} - {r["task_id"] for r in rows}
            ),
        },
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare", "probe", "worker"])
    p.add_argument("--root", default="data/appworld")
    p.add_argument("--output", required=True)
    p.add_argument("--plan")
    p.add_argument("--task")
    p.add_argument("--previous", help="Continue only unattempted prefix remainder")
    a = p.parse_args()
    if a.command == "prepare":
        prepare(a.root, a.output)
    elif a.command == "probe":
        probe(a.root, a.plan, a.output, a.previous)
    else:
        worker(a.root, a.task, a.output)


if __name__ == "__main__":
    main()
