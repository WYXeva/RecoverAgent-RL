"""Independent frozen C244 evaluation; reuse the Student engine without edits.

Custom Full732 research development results, not official benchmark scores.
No training, checkpoint selection, task replacement or automatic retries.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from . import student, student_evaluation
from .demonstrations import sha256
from .evaluation import environment_check, evaluate_here
from .full732_sft_a100_runtime import verify_lock as verify_training_lock
from .full732_sft_data import ROOT, SPLIT, MANIFEST, FORMAT
from .io import digest, files_manifest, read_json, write_json
from .unified_split import validate_split

SCOPE = "custom_full732_C244_M1_development_20261030"
LOCK = ROOT / "configs/full732_c244_20261030_lock.json"
PROTOCOL = ROOT / "configs/full732_c244_20261030.json"


def c_tasks():
    path = ROOT / "configs/unified_task_split_full_pool_candidate.json"
    if sha256(path) != SPLIT:
        raise ValueError("Frozen Full732 assignment changed")
    plan = read_json(path)
    validate_split(plan)
    rows = sorted(
        (r for r in plan["rows"] if r["role"] == "shared_validation"),
        key=lambda r: r["task_id"],
    )
    if len(rows) != 244 or len({r["scenario_id"] for r in rows}) != 244:
        raise ValueError("Incomplete C244")
    return plan, rows


def verify_lock(runtime=student.V6_RUNTIME):
    lock = read_json(LOCK)
    if (
        runtime != student.V6_RUNTIME
        or lock["scope"] != SCOPE
        or digest(lock["session_contract"]) != student.V6_CONTRACT_SHA256
        or lock["training_runtime_sha256"] != verify_training_lock()
    ):
        raise ValueError("Wrong C244 execution contract")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("C244 source differs: " + name)
    return {
        "revision": runtime,
        "scope": SCOPE,
        "lock_sha256": sha256(LOCK),
        "session_contract": lock["session_contract"],
    }


def task_provenance(root, task, split):
    plan, rows = c_tasks()
    row = next((r for r in rows if r["task_id"] == task), None)
    if row is None or row["official_split"] != split:
        raise ValueError(
            "Only the frozen C244 task and original provenance are allowed"
        )
    data = Path(root) / "data"
    if (data / "version.txt").read_text().strip() != plan["source"]["data_version"]:
        raise ValueError("AppWorld data version changed")
    source = data / "datasets" / (split + ".txt")
    if (
        sha256(source) != plan["source"]["split_files"][split]
        or task not in source.read_text().splitlines()
    ):
        raise ValueError("Original task provenance changed")
    for name in (
        "specs.json",
        "dbs",
        "ground_truth/evaluation.py",
        "ground_truth/metadata.json",
    ):
        if not (data / "tasks" / task / name).exists():
            raise ValueError("Missing initialization/scoring artifact")
    return row


class CAdapter(student_evaluation.EvaluationAdapter):
    """Same public AppWorld initialization, with a fixed custom C244 boundary."""

    def __init__(self, root, task_id, experiment_name, split):
        # Keep official train/dev entry points intact. Initialization options
        # match EvaluationAdapter; only frozen C244 membership is added here.
        from appworld import AppWorld, update_root
        from appworld.common.path_store import path_store
        import importlib.metadata
        from . import APPWORLD_REVISION

        task_provenance(root, task_id, split)
        direct = importlib.metadata.distribution("appworld").read_text(
            "direct_url.json"
        )
        if not direct or APPWORLD_REVISION not in direct:
            raise ValueError("Pinned AppWorld source required")
        update_root(str(Path(root).resolve()))
        output = (
            Path(path_store.experiment_outputs) / experiment_name / "tasks" / task_id
        )
        if output.exists():
            raise FileExistsError("Refusing to reuse environment output")
        self.task_id, self.experiment_name = task_id, experiment_name
        self.world = AppWorld(
            task_id=task_id,
            experiment_name=experiment_name,
            load_ground_truth=False,
            include_direct_functions=True,
            direct_function_separator="__",
            random_seed=42,
            max_interactions=sys.maxsize,
            max_api_calls_per_interaction=1,
            timeout_seconds=60,
            allow_datetime_change=False,
            add_login_shortcut=False,
        )
        self.catalogue = self.world.task.api_docs.function_calling()
        if any(t["function"]["name"].startswith("admin__") for t in self.catalogue):
            raise ValueError("Admin tools must not be exposed")
        self.versions = {
            "appworld": importlib.metadata.version("appworld"),
            "revision": APPWORLD_REVISION,
            "data_version": self.world.task.db_version,
        }


def evaluate_terminal(task, experiment, root, split, output):
    task_provenance(root, task, split)
    destination = Path(output) / "official_evaluation.json"
    with (Path(output) / "evaluation_worker.log").open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "recoveragent.full732_c244",
                "official",
                "--task",
                task,
                "--experiment",
                experiment,
                "--root",
                root,
                "--split",
                split,
                "--output",
                str(destination),
            ],
            check=True,
            timeout=120,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    return read_json(destination)


def isolated_module(module, name):
    spec = importlib.util.spec_from_file_location(
        "recoveragent." + name, module.__file__
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def engine():
    # Current adapter acquired A244 research support after the historical v6
    # lock. Freeze the current source separately; never rewrite that old lock.
    session = isolated_module(student, "_c244_student")
    session.verify_student_runtime = verify_lock
    runner = isolated_module(student_evaluation, "_c244_evaluation")
    runner.StudentSession = session.StudentSession
    runner.EvaluationAdapter = CAdapter
    runner.verify_execution_runtime = verify_lock
    runner.evaluate_terminal = evaluate_terminal
    return runner


def verified_checkpoint(path):
    path = Path(path)
    report = read_json(path / "recoveragent_checkpoint.json")
    if any(
        report.get(k) != v
        for k, v in {
            "status": "PASS",
            "runtime_contract": FORMAT,
            "profile": "formal",
            "source_manifest_sha256": MANIFEST,
            "checkpoint_reloaded": True,
            "parameter_update_and_reload_pass": True,
            "training_examples": 2070,
            "source_trajectories": 138,
            "training_validation": None,
            "runtime_lock_sha256": verify_training_lock(),
        }.items()
    ):
        raise ValueError("M1 formal checkpoint has not passed its training gates")
    if (
        report["trainable_before_hash"] == report["trainable_after_hash"]
        or report["trainable_after_hash"] != report["restored_trainable_hash"]
    ):
        raise ValueError("M1 parameter/reload proof differs")
    if [r["profile"] for r in report["prerequisites"]] != ["short_smoke", "long_smoke"]:
        raise ValueError("Missing M1 smoke gates")
    actual = files_manifest(path)
    actual.pop("recoveragent_checkpoint.json")
    if actual != report["files"]:
        raise ValueError("M1 checkpoint/evidence changed")
    return report


def validate_config(config):
    protocol = read_json(PROTOCOL)
    _, rows = c_tasks()
    cases = [
        {
            "task_id": r["task_id"],
            "scenario_id": r["scenario_id"],
            "partition": "C244",
            "split": r["official_split"],
        }
        for r in rows
    ]
    if config["scope"] != SCOPE or config["cases"] != cases:
        raise ValueError("C244 replacement, removal, or reordering")
    for key, value in protocol["execution"].items():
        if key != "appworld_root" and config[key] != value:
            raise ValueError("Frozen C244 execution differs: " + key)
    if config["expected_policy"] != protocol["model_identity"] | {
        "checkpoint_id": "adapter:" + config["m1_parameter_hash"]
    }:
        raise ValueError("C244 checkpoint identity differs")
    if config["inference_url"] != "http://127.0.0.1:8768":
        raise ValueError("C244 requires the private fixed local backend")
    verify_lock()


def summarize(config, rows):
    valid = [
        r
        for r in rows
        if r["status"] == "valid" and type(r["official_success"]) is bool
    ]
    success = sum(r["official_success"] for r in valid)
    complete = len(rows) == len(valid) == 244
    return {
        "scope": SCOPE,
        "stage": "M1",
        "fixed_denominator": 244,
        "attempted": len(rows),
        "valid": len(valid),
        "success": success,
        "failure": len(valid) - success,
        "unknown": len(rows) - len(valid),
        "infrastructure_error": sum(
            r["status"] == "infrastructure_error" for r in rows
        ),
        "complete_valid_evaluation": complete,
        "TGC": success / 244 if complete else None,
        "unattempted": config["cases"][len(rows) :],
        "rows": rows,
        "training": False,
        "checkpoint_selection": False,
        "official_benchmark_claim": False,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare", "collect", "worker", "official"])
    p.add_argument("--output", required=True)
    for name in ("config", "checkpoint", "root", "task", "split", "experiment"):
        p.add_argument("--" + name)
    p.add_argument("--case-index", type=int)
    a = p.parse_args()
    verify_lock()
    if a.command == "official":
        from appworld import update_root

        task_provenance(a.root, a.task, a.split)
        environment_check()
        update_root(str(Path(a.root).resolve()))
        write_json(a.output, evaluate_here(a.task, a.experiment))
        return
    if a.command == "prepare":
        if Path(a.output).exists():
            raise FileExistsError(a.output)
        report = verified_checkpoint(a.checkpoint)
        protocol = read_json(PROTOCOL)
        _, tasks = c_tasks()
        for row in tasks:
            task_provenance(a.root, row["task_id"], row["official_split"])
        config = dict(
            protocol["execution"],
            scope=SCOPE,
            appworld_root=str(Path(a.root).resolve()),
            inference_url="http://127.0.0.1:8768",
            m1_parameter_hash=report["trainable_after_hash"],
            expected_policy=protocol["model_identity"]
            | {"checkpoint_id": "adapter:" + report["trainable_after_hash"]},
            checkpoint_receipt_sha256=sha256(
                Path(a.checkpoint) / "recoveragent_checkpoint.json"
            ),
            cases=[
                {
                    "task_id": r["task_id"],
                    "scenario_id": r["scenario_id"],
                    "partition": "C244",
                    "split": r["official_split"],
                }
                for r in tasks
            ],
        )
        validate_config(config)
        write_json(a.output, config)
        return
    config = read_json(a.config)
    validate_config(config)
    if a.command == "worker":
        result = engine().run_episode(
            config, config["cases"][a.case_index], "base_sft", a.output
        )
        result["engine_scope"] = result.pop("scope")
        result.update(scope=SCOPE, stage="M1", official_benchmark_claim=False)
        write_json(Path(a.output) / "full732_result.json", result)
        return
    output = Path(a.output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config.json", config)
    rows = []
    for i, case in enumerate(config["cases"]):
        target = output / case["task_id"]
        try:
            with (output / (case["task_id"] + ".log")).open("x") as log:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "recoveragent.full732_c244",
                        "worker",
                        "--config",
                        str(Path(a.config).resolve()),
                        "--case-index",
                        str(i),
                        "--output",
                        str(target.resolve()),
                    ],
                    check=True,
                    timeout=config["student_limits"]["wall_seconds"] + 180,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=dict(os.environ),
                )
            row = read_json(target / "full732_result.json")
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            row = dict(
                case,
                status="infrastructure_error",
                official_success=None,
                error_type=type(exc).__name__,
                usage_unknown=True,
            )
            write_json(output / (case["task_id"] + ".worker_error.json"), row)
        rows.append(row)
        write_json(
            output / "progress" / f"{len(rows):03d}.json", summarize(config, rows)
        )
        print(
            json.dumps(
                {
                    "attempted": len(rows),
                    "task": case["task_id"],
                    "status": row["status"],
                    "success": row["official_success"],
                }
            ),
            flush=True,
        )
        if row["status"] == "infrastructure_error":
            break
    write_json(output / "summary.json", summarize(config, rows))


if __name__ == "__main__":
    main()
