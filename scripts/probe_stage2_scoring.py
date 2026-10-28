"""Stage 2 only: three train states, sealed Teacher DBs, official scoring, no model.

Raw trackers/history stay in private runs/. This does not change production gates.
Run each state in a fresh process; evaluation also runs in a separate process.
"""

import argparse
from pathlib import Path
import subprocess
import sys
import time

from recoveragent.adapter import AppWorldAdapter
from recoveragent.demonstrations import sha256
from recoveragent.evaluation import environment_check, evaluate_here
from recoveragent.io import digest, files_manifest, read_json, write_json
from recoveragent.teacher_history import validate_history

SOURCES = {
    "76f2c72_1": "runs/base_sft_v1_20261021_b01/teacher_8bd716312dd2",
    "d0b1f43_1": "runs/base_sft_v1_20261021_b03/teacher_c627aa5cfc03",
    "07b42fd_1": "runs/base_sft_v1_20261021_b05/teacher_935fa94ac30d",
}
DATASET = Path("data/base_sft_v1")
DATASET_SHA = "5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c"


def sealed_source(task):
    """Chain the private terminal to the immutable accepted dataset, not a claim."""
    if task not in SOURCES:
        raise ValueError("Outside the frozen Stage 2 train diagnostic set")
    if sha256(DATASET / "manifest.json") != DATASET_SHA:
        raise ValueError("Trusted dataset manifest changed")
    manifest = read_json(DATASET / "manifest.json")
    index = "control/all_samples.jsonl"
    if sha256(DATASET / index) != manifest["files"][index]:
        raise ValueError("Accepted sample index changed")
    import json

    rows = [json.loads(line) for line in (DATASET / index).read_text().splitlines()]
    source = Path(SOURCES[task])
    sample = next(
        r for r in rows if r["task_id"] == task and r["trajectory_id"] == source.name
    )
    for file, key in (
        ("trajectory.json", "trajectory_sha256"),
        ("evaluation.json", "evaluation_sha256"),
    ):
        if sha256(source / file) != sample["provenance"][key]:
            raise ValueError("Sealed Teacher source changed")
    evaluation = read_json(source / "evaluation.json")
    terminal = read_json(source / "terminal/manifest.json")
    if (
        sample["split"] != "train"
        or sample["actor_role"] != "teacher"
        or evaluation["task_id"] != task
        or evaluation["reference"] != "original_task"
        or evaluation["success"] is not True
        or evaluation["official"]["success"] is not True
        or terminal["task_id"] != task
        or terminal["split"] != "train"
        or terminal["checkpoint_id"] != sample["provenance"]["terminal_checkpoint_id"]
        or terminal["checkpoint_id"] != evaluation["terminal_checkpoint_id"]
        or digest({k: v for k, v in terminal.items() if k != "checkpoint_id"})
        != terminal["checkpoint_id"]
    ):
        raise ValueError("Untrusted positive terminal identity")
    actual = files_manifest(source / "terminal")
    actual.pop("manifest.json")
    if actual != terminal["files"]:
        raise ValueError("Sealed terminal files changed")
    context = read_json(source / "terminal/context.json")
    validate_history(context["messages"])
    if (
        digest(context["messages"]) != terminal["history_hash"]
        or digest(context["tools"]) != terminal["tool_schema_hash"]
    ):
        raise ValueError("Terminal history/schema differs")
    return source, sample, terminal


def terminal_verdict(evaluation):
    """Diagnostic evidence level only, never a production reward or unlock."""
    if evaluation.get("status") != "valid" or not evaluation.get(
        "official_result_complete"
    ):
        return "NOT_VERIFIED"
    if evaluation.get("success") is True:
        return "PASS"
    if evaluation.get("success") is False:
        return "FAIL"
    return "NOT_VERIFIED"


def score(root, task, experiment, output):
    from appworld import load_task_ids, update_root
    from appworld.evaluator import TestTracker

    update_root(str(root.resolve()))
    if task not in SOURCES or task not in load_task_ids("train"):
        raise ValueError("Only frozen official train tasks are allowed")
    environment_check()
    traces = []
    code = TestTracker.__exit__.__code__
    previous = sys.gettrace()

    def observe(frame, event, arg):
        if event == "call" and frame.f_code is code and frame.f_locals["exc_type"]:
            tb, frames = frame.f_locals["traceback"], []
            while tb:
                filename = Path(tb.tb_frame.f_code.co_filename)
                parts = filename.parts
                # No source text, operand values, task instructions or frame locals.
                start = (
                    parts.index("appworld")
                    if "appworld" in parts
                    else max(0, len(parts) - 4)
                )
                frames.append(
                    {
                        "file": "/".join(parts[start:]),
                        "line": tb.tb_lineno,
                        "function": tb.tb_frame.f_code.co_name,
                    }
                )
                tb = tb.tb_next
            traces.append(
                {
                    "exception_type": frame.f_locals["exc_type"].__name__,
                    "frames": frames,
                }
            )
        return None

    try:
        sys.settrace(
            observe
        )  # Read-only, coexists with evaluate_here's profile observer.
        result = evaluate_here(task, experiment)
    finally:
        sys.settrace(previous)
    write_json(output / "evaluation.json", result)
    write_json(output / "official_tracker.json", result.get("official"))
    write_json(output / "exception_frames.json", traces)


def probe(root, task, state, output):
    if state not in {"initial", "positive"}:
        raise ValueError("Only initial or sealed positive state diagnostics")
    source, sample, terminal = sealed_source(task)  # Before world initialization.
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    experiment = "stage2_" + digest(str(output.resolve()))[:24]
    record = {
        "task_id": task,
        "state": state,
        "experiment": experiment,
        "status": "NOT_VERIFIED",
        "model_calls": 0,
        "teacher_calls": 0,
        "gpu_calls": 0,
        "training_steps": 0,
        "public_api_calls": 0,
        "source": str(source),
        "source_manifest_sha256": sha256(source / "terminal/manifest.json"),
        "source_evaluation_sha256": sha256(source / "evaluation.json"),
        "script_sha256": sha256(__file__),
    }
    adapter = None
    try:
        record["environment"] = environment_check()
        adapter = AppWorldAdapter(root, task, experiment)
        record["initial_state_hash"] = adapter.fingerprint()
        if record["initial_state_hash"] != sample["initial_state_hash"]:
            raise ValueError("Original task initial state differs from sealed source")
        for key in ("revision", "data_version", "task_input_files", "base_db_files"):
            if adapter.versions[key] != terminal["versions"][key]:
                raise ValueError("Original task/base input differs: " + key)
        if state == "positive":
            adapter.import_state(source / "terminal/dbs", "sealed_positive")
            adapter.restore_runtime(read_json(source / "terminal/runtime.json"))
            if adapter.fingerprint() != terminal["logical_state_hash"]:
                raise ValueError("Restored DB differs from sealed terminal")
            record["restored_db_hash_matches"] = True
        record["state_hash"] = adapter.fingerprint()
        record["task_completed"] = adapter.world.task_completed()
        from appworld.apps.supervisor.constants import NOT_GIVEN_ANSWER

        task_rows = adapter.world.models.supervisor.Task.all()
        record["answer_is_not_given_sentinel"] = (
            bool(task_rows) and task_rows[0].answer == NOT_GIVEN_ANSWER
        )
        adapter.save_terminal()
        adapter.close()
        adapter = None
        dbs = root / "experiments/outputs" / experiment / "tasks" / task / "dbs"
        before = files_manifest(dbs)
        with (output / "evaluation_worker.log").open("x") as log:
            subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--root",
                    str(root),
                    "--task",
                    task,
                    "--state",
                    "score",
                    "--experiment",
                    experiment,
                    "--output",
                    str(output),
                ],
                check=True,
                timeout=120,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        record["scoring_did_not_modify_saved_dbs"] = before == files_manifest(dbs)
        if not record["scoring_did_not_modify_saved_dbs"]:
            raise ValueError("Evaluation modified saved terminal DBs")
        evaluation = read_json(output / "evaluation.json")
        record.update(
            {
                k: evaluation.get(k)
                for k in (
                    "category",
                    "success",
                    "official_success",
                    "official_result_complete",
                    "exception_types",
                )
            }
        )
        record["evaluation_status"] = evaluation["status"]
        record["status"] = (
            terminal_verdict(evaluation) if state == "positive" else "RECORDED"
        )
        official = evaluation.get("official", {})
        record["tracker_counts"] = {
            "num_tests": official.get("num_tests"),
            "passes": len(official.get("passes", [])),
            "failures": len(official.get("failures", [])),
        }
    except Exception as exc:
        import traceback

        record["error_type"] = type(exc).__name__
        (output / "exception.log").write_text(traceback.format_exc())
    finally:
        if adapter:
            adapter.close()
        record["seconds"] = time.perf_counter() - started
        write_json(output / "result.json", record)
        write_json(output / "manifest.json", {"files": files_manifest(output)})
    print(
        {
            k: record.get(k)
            for k in ("task_id", "state", "status", "category", "error_type")
        },
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=tuple(SOURCES), required=True)
    parser.add_argument(
        "--state", choices=("initial", "positive", "score"), required=True
    )
    parser.add_argument("--root", type=Path, default=Path("data/appworld"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiment")
    args = parser.parse_args()
    if args.state == "score":
        if not args.experiment or not args.experiment.startswith("stage2_"):
            parser.error("Evaluation requires this diagnostic's isolated experiment")
        score(args.root, args.task, args.experiment, args.output)
    else:
        probe(args.root, args.task, args.state, args.output)


if __name__ == "__main__":
    main()
