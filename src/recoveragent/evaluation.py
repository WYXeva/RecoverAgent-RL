"""Isolated controller-side official evaluation, with read-only exception tracing."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from .io import read_json, write_json


def environment_check():
    from appworld.apps.lib.models.db import Database, get_db_engine

    engine = get_db_engine(":memory:harness_check/spotify.db")
    try:
        actual = Database._get_engine_to_details(engine)[2]
        if actual != ":memory:harness_check":
            raise RuntimeError("AppWorld memory URI roundtrip failed")
    finally:
        engine.dispose()
    return {
        "sqlalchemy": importlib.metadata.version("sqlalchemy"),
        "sqlmodel": importlib.metadata.version("sqlmodel"),
        "uri_roundtrip": True,
    }


def evaluate_here(task_id, experiment_name):
    """Observe official tracker exceptions without changing any assertion/outcome.

    suppress_errors=False stops at the first ordinary assertion and could miss
    later runtime failures. Profile the unmodified tracker instead, keep its full
    report on the controller side, and fail closed on non-AssertionError failures.
    Neither exception bodies nor hidden labels are returned to the Actor.
    """
    from appworld.evaluator import TestTracker, evaluate_task

    errors = []
    code = TestTracker.__exit__.__code__
    previous = sys.getprofile()

    def observe(frame, event, arg):
        if event == "call" and frame.f_code is code:
            exc_type = frame.f_locals.get("exc_type")
            if exc_type is not None and not issubclass(exc_type, AssertionError):
                errors.append(exc_type.__name__)

    try:
        sys.setprofile(observe)
        tracker = evaluate_task(
            task_id,
            experiment_name=experiment_name,
            suppress_errors=True,
            save_report=False,
        )
        return {
            "status": "evaluation_error" if errors else "valid",
            "success": bool(tracker.success) if not errors else None,
            "exception_types": sorted(set(errors)),
            "official": tracker.to_dict(),
        }
    except Exception as exc:
        return {
            "status": "evaluation_error",
            "success": None,
            "exception_types": [type(exc).__name__],
        }
    finally:
        sys.setprofile(previous)


def evaluate_isolated(task_id, experiment_name, root, directory, timeout=120):
    destination = Path(directory) / "isolated_evaluation.json"
    with (Path(directory) / "evaluation_worker.log").open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "recoveragent.evaluation",
                "--task",
                task_id,
                "--experiment",
                experiment_name,
                "--root",
                str(Path(root).resolve()),
                "--output",
                str(destination.resolve()),
            ],
            check=True,
            timeout=timeout,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    return read_json(destination)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True)
    p.add_argument("--experiment", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    from appworld import load_task_ids, update_root

    environment = environment_check()
    update_root(a.root)
    if a.task not in load_task_ids("train"):
        raise ValueError("Harness evaluation only accepts official train tasks")
    result = evaluate_here(a.task, a.experiment)
    result.update(environment=environment, task_id=a.task, experiment=a.experiment)
    write_json(a.output, result)
    print(json.dumps({k: result[k] for k in ("status", "success", "exception_types")}))


if __name__ == "__main__":
    main()
