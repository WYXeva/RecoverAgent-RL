"""Isolated controller-side official evaluation, with read-only exception tracing."""

import argparse
import ast
import importlib.metadata
import json
import linecache
from pathlib import Path
import subprocess
import sys

from .io import read_json, write_json

CLASSIFICATION_REVISION = "official_result_integrity_20261114_v2"


def invalid_predicted_number(exc, tb):
    """Recognize only the official conversion of the submitted numeric answer."""
    if not isinstance(exc, ValueError) or tb is None:
        return False
    line = linecache.getline(tb.tb_frame.f_code.co_filename, tb.tb_lineno).strip()
    try:
        statement = ast.parse(line).body[0]
    except (SyntaxError, IndexError):
        return False
    return (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == "predicted_answer"
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id == "float"
        and len(statement.value.args) == 1
        and isinstance(statement.value.args[0], ast.Name)
        and statement.value.args[0].id == "predicted_answer"
        and not statement.value.keywords
        and "float" not in tb.tb_frame.f_locals
        and "float" not in tb.tb_frame.f_globals
    )


def invalid_normalized_answer(exc, tb, task_id):
    """Pinned TestTracker.case(... normalize=float): only its right-hand operand.

    The left/reference conversion has already succeeded. Inspect code and object
    identity only; never expose reference values or rerun the normalization.
    """
    if not isinstance(exc, (ValueError, TypeError)) or tb is None:
        return False
    frames = []
    while tb is not None:
        frames.append(tb)
        tb = tb.tb_next
    leaf = frames[-1]
    frame = leaf.tb_frame
    if (
        Path(frame.f_code.co_filename).parts[-3:]
        != ("appworld", "common", "evaluation.py")
        or frame.f_code.co_name != "assert_plus"
        or frame.f_locals.get("normalize") is not float
        or linecache.getline(frame.f_code.co_filename, leaf.tb_lineno).strip()
        != "right = normalize(right)"
    ):
        return False
    for item in frames[:-1]:
        caller = item.tb_frame
        if Path(caller.f_code.co_filename).parts[-4:] != (
            "tasks",
            task_id,
            "ground_truth",
            "evaluation.py",
        ):
            continue
        try:
            tree = ast.parse("".join(linecache.getlines(caller.f_code.co_filename)))
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and node.lineno <= item.tb_lineno <= node.end_lineno
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "test"
                and node.func.attr == "case"
                and len(node.args) == 3
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "ground_truth_answer"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "=="
                and isinstance(node.args[2], ast.Name)
                and node.args[2].id == "predicted_answer"
                and any(
                    k.arg == "normalize"
                    and isinstance(k.value, ast.Name)
                    and k.value.id == "float"
                    for k in node.keywords
                )
                and "predicted_answer" in caller.f_locals
                and frame.f_locals.get("right") is caller.f_locals["predicted_answer"]
            ):
                return True
    return False


def exception_observation(exc, tb, task_id):
    """Controller-only evidence: exception class/origin, never values or locals.

    A complete official test may fail while indexing a missing task object.
    Accept those *direct task evaluation* failures, not the same exception in
    a DB loader or arbitrary library. Unknown origins remain unknown.
    """
    chain, current = [], exc
    while current is not None and id(current) not in {id(e) for e in chain}:
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(
        isinstance(e, (OSError, MemoryError, ImportError))
        or type(e).__module__.startswith(("sqlalchemy", "sqlite3", "sqlmodel"))
        for e in chain
    ):
        category = "environment_error"
    elif isinstance(exc, AssertionError):
        category = "task_assertion"
    elif invalid_normalized_answer(exc, tb, task_id):
        category = "task_answer_failure"
    else:
        while tb is not None and tb.tb_next is not None:
            tb = tb.tb_next
        parts = Path(tb.tb_frame.f_code.co_filename).parts if tb else ()
        direct_task = parts[-4:] == ("tasks", task_id, "ground_truth", "evaluation.py")
        category = (
            "task_answer_failure"
            if direct_task and invalid_predicted_number(exc, tb)
            else "task_state_failure"
            if direct_task
            and isinstance(exc, (KeyError, IndexError, UnboundLocalError))
            else "unknown_exception"
        )
    return {"exception_type": type(exc).__name__, "category": category}


def classify_official(official, observations):
    """Keep official scoring separate from whether it is usable as a reward."""
    complete = (
        type(official.get("success")) is bool
        and type(official.get("num_tests")) is int
        and official["num_tests"] > 0
        and isinstance(official.get("passes"), list)
        and isinstance(official.get("failures"), list)
        and len(official["passes"]) + len(official["failures"]) == official["num_tests"]
        and official["success"] == (not official["failures"])
        and len(observations) == len(official["failures"])
    )
    categories = {o["category"] for o in observations}
    if "environment_error" in categories:
        category = "environment_error"
    elif not complete or "unknown_exception" in categories:
        category = "unknown"
    else:
        category = "task_success" if official["success"] else "task_failure"
    valid = category in {"task_success", "task_failure"}
    return {
        "classification_revision": CLASSIFICATION_REVISION,
        "status": "valid" if valid else "evaluation_error",
        "category": category,
        "success": official["success"] if valid else None,
        "official_success": official.get("success"),
        "official_result_complete": complete,
        "observations": observations,
        "exception_types": sorted(
            {
                o["exception_type"]
                for o in observations
                if o["category"] != "task_assertion"
            }
        ),
        "official": official,
    }


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
    report on the controller side, and classify integrity separately from scores.
    Neither exception bodies nor hidden labels are returned to the Actor.
    """
    from appworld.evaluator import TestTracker, evaluate_task

    errors = []
    code = TestTracker.__exit__.__code__
    previous = sys.getprofile()

    def observe(frame, event, arg):
        if event == "call" and frame.f_code is code:
            exc_type = frame.f_locals.get("exc_type")
            if exc_type is not None:
                errors.append(
                    exception_observation(
                        frame.f_locals.get("exc_value"),
                        frame.f_locals.get("traceback"),
                        task_id,
                    )
                )

    try:
        sys.setprofile(observe)
        tracker = evaluate_task(
            task_id,
            experiment_name=experiment_name,
            suppress_errors=True,
            save_report=False,
        )
        return classify_official(tracker.to_dict(), errors)
    except Exception as exc:
        return {
            "status": "evaluation_error",
            "classification_revision": CLASSIFICATION_REVISION,
            "category": "environment_error"
            if exception_observation(exc, exc.__traceback__, task_id)["category"]
            == "environment_error"
            else "unknown",
            "success": None,
            "official_success": None,
            "official_result_complete": False,
            "exception_types": [type(exc).__name__],
        }
    finally:
        sys.setprofile(previous)


def evaluate_isolated(
    task_id, experiment_name, root, directory, timeout=120, *, research=None
):
    destination = Path(directory) / "isolated_evaluation.json"
    extra = []
    if research is not None:
        from .v2_tasks import SCOPE as V2_SCOPE

        if research.get("scope") == V2_SCOPE:
            from .v2_tasks import validate_context
        else:
            from .full732_recovery_tasks import validate_context

        validate_context(task_id, research)
        context_path = Path(directory) / "evaluation_research.json"
        write_json(context_path, research)
        extra = ["--research", str(context_path.resolve())]
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
                *extra,
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
    p.add_argument("--research")
    a = p.parse_args()
    from appworld import load_task_ids, update_root

    environment = environment_check()
    update_root(a.root)
    if a.research:
        research = read_json(a.research)
        from .v2_tasks import SCOPE as V2_SCOPE

        if research.get("scope") == V2_SCOPE:
            from .v2_tasks import authorize_task
        else:
            from .full732_recovery_tasks import authorize_task

        authorize_task(a.root, a.task, research)
    elif a.task not in load_task_ids("train"):
        raise ValueError("Harness evaluation only accepts official train tasks")
    result = evaluate_here(a.task, a.experiment)
    result.update(environment=environment, task_id=a.task, experiment=a.experiment)
    write_json(a.output, result)
    print(json.dumps({k: result[k] for k in ("status", "success", "exception_types")}))


if __name__ == "__main__":
    main()
