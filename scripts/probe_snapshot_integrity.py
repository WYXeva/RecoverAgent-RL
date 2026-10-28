"""Stage 2B diagnostic only: fixed train reference, metadata, independent restore.

No model, no historical repair and no production gate changes. Raw official
trackers/logs stay under runs/. Each command requires a new output directory.
"""

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from recoveragent.adapter import AppWorldAdapter
from recoveragent.demonstrations import sha256
from recoveragent.evaluation import (
    classify_official,
    environment_check,
    evaluate_isolated,
    exception_observation,
)
from recoveragent.io import digest, files_manifest, read_json, write_json

TASK = "07b42fd_1"
REVISION = "stage2b-snapshot-scoring-integrity-v1"
# AppWorld freezes time globally. A tuple keeps this real clock out of freezegun's
# module-attribute replacement, including when world initialization raises.
CLOCK = (time.perf_counter,)


def metadata_summary(value):
    return {
        "sha256": digest(value),
        "empty": not value,
        "keys": sorted(
            f"{app}.{name}" for app, models in value.items() for name in models
        ),
    }


def state_observation(world):
    """Read registry without defaultdict lookups that would insert empty entries."""
    from appworld.apps.lib.models.db import ModelHashHandler

    home = world.models_to_db_home_path
    before = json.loads(json.dumps(ModelHashHandler.data.get(home, {})))
    rows = AppWorldAdapter.fingerprint(SimpleNamespace(world=world))
    changes = world.models.get_tracked_db_changes()
    after = json.loads(json.dumps(ModelHashHandler.data.get(home, {})))
    if before != after:
        raise RuntimeError("Observation changed the model hash registry")
    return {
        "db_fingerprint": rows,
        "metadata": metadata_summary(before),
        "declared_home": home,
        "actual_homes": sorted(
            {m.SQLModel.db.home_path for m in world.models.values()}
        ),
        "changes_sha256": digest(changes),
        "changes_counts": {app: len(value) for app, value in changes.items()},
    }


def disk_observation(path):
    path = Path(path)
    return {
        "path": str(path),
        "files": files_manifest(path),
        "metadata": metadata_summary(read_json(path / "model_hashes.json")),
    }


def verify_export(source, expected_files):
    """Trusted parent receipt anchors files before import; no self-re-signing."""
    if files_manifest(source) != expected_files:
        raise ValueError("Export differs from the live process receipt")


def integrity_verdict(live, restored):
    """Evidence gate, NOT a reward. Empty hashes alone are not a rejection rule.

    Requires paired full official results, metadata, logical rows, tracking and
    unchanged exported bytes. Missing observations never pass by equality of None.
    """
    try:
        before, after = live["before_save"], restored["after_import"]
        saved, exported, final = (
            live["saved"],
            live["exported"],
            restored["saved_terminal"],
        )
        a, b = live["evaluation"], restored["evaluation"]
        required = ("db_fingerprint", "metadata", "changes_sha256", "changes_counts")
        checks = {
            "rows_and_tracking_equal": all(before[k] == after[k] for k in required),
            "metadata_persisted": all(
                item["metadata"] == before["metadata"]
                for item in (live["after_save"], saved, exported, final)
            ),
            "snapshot_files_equal": saved["files"]
            == exported["files"]
            == final["files"],
            "export_unchanged": restored["export_unchanged"] is True,
            "evaluation_db_unchanged": restored["evaluation_db_unchanged"] is True,
            "complete_official_results": all(
                e["status"] == "valid" and e["official_result_complete"] is True
                for e in (a, b)
            ),
            "official_results_equal": a["official"] == b["official"],
            "independent_process": live["pid"] != restored["pid"],
            "trusted_live_receipt": restored["live_receipt_sha256"]
            == live["receipt_sha256"],
            "write_metadata_present": not live["write_observed"]
            or not before["metadata"]["empty"],
        }
    except (KeyError, TypeError):
        return {"status": "NOT_VERIFIED", "reason": "incomplete_evidence"}
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def uri_probe(output):
    """No task/world or business write. Inspect unmodified official URI parser."""
    from appworld.apps.lib.models.db import Database, get_db_engine

    requested = ":memory:stage2b_uri_probe"
    engine = get_db_engine(requested + "/spotify.db")
    try:
        url, _, actual, _, _ = Database._get_engine_to_details(engine)
    finally:
        engine.dispose()
    return {
        "sqlalchemy": importlib.metadata.version("sqlalchemy"),
        "requested_home": requested,
        "actual_home": actual,
        "rendered_url": url,
        "home_matches": requested == actual,
        "task_executions": 0,
    }


def live_probe(root, output):
    """Observe the exact official single-task verification; no solution copy."""
    import os
    from appworld import AppWorld, load_task_ids, update_root
    from appworld.evaluator import TestTracker, evaluate_task
    from appworld.verify import _verify_task

    environment = environment_check()
    update_root(str(root.resolve()))
    if TASK not in load_task_ids("train"):
        raise ValueError("Fixed control must be official train")
    experiment = "stage2b_live_" + digest(str(output.resolve()))[:20]
    if (root / "experiments/outputs" / experiment).exists():
        raise FileExistsError("Refusing to overwrite an experiment")
    record = {"pid": os.getpid(), "environment": environment, "experiment": experiment}
    errors, trackers, save_boundaries = [], [], []
    previous = sys.getprofile()

    def observe(frame, event, arg):
        code = frame.f_code
        if code is AppWorld._save_state.__code__ and event == "call":
            world = frame.f_locals["self"]
            if getattr(world, "num_interactions", 0):
                save_boundaries.append(
                    {
                        "directory": frame.f_locals["directory"],
                        "state": state_observation(world),
                    }
                )
                write_json(
                    output / f"save_boundary_{len(save_boundaries):02d}.json",
                    save_boundaries[-1],
                )
        if code is AppWorld.execute.__code__ and event == "call":
            record["initial"] = state_observation(frame.f_locals["self"])
        if code is AppWorld.evaluate.__code__ and event == "call":
            world = frame.f_locals["self"]
            record["before_save"] = state_observation(world)
            checkpoint = "stage2b_before_export"
            world.save_state(checkpoint)
            record["after_save"] = state_observation(world)
            record["saved"] = disk_observation(
                Path(world.output_checkpoints_directory) / checkpoint
            )
            # Bind only the existing adapter's save/copy methods to this official
            # world. Do not initialize a second world or alter execution flags.
            AppWorldAdapter.export_state(
                SimpleNamespace(world=world), "stage2b_export", output / "export"
            )
            record["exported"] = disk_observation(output / "export")
            record["api_calls"] = len(world.requester.request_tracker.requests)
            write_json(output / "boundaries.json", record)
        if code is TestTracker.__exit__.__code__ and event == "call":
            if frame.f_locals["exc_type"]:
                errors.append(
                    exception_observation(
                        frame.f_locals["exc_value"], frame.f_locals["traceback"], TASK
                    )
                )
        if (
            code is evaluate_task.__code__
            and event == "return"
            and isinstance(arg, TestTracker)
        ):
            trackers.append(arg.to_dict())
            write_json(output / "official_tracker.json", arg.to_dict())

    try:
        sys.setprofile(observe)
        result = _verify_task(
            {
                "task_id": TASK,
                "experiment_name": experiment,
                "raise_on_failure": False,
                "ground_truth_mode": "full",
            }
        )
    finally:
        sys.setprofile(previous)
    if len(trackers) != 1:
        raise RuntimeError("Expected exactly one official verification")
    record["evaluation"] = classify_official(trackers[0], errors)
    record["upstream_success"] = result[1]
    # Actual persisted SQL changes, not a task-name heuristic or model claim.
    record["write_observed"] = any(
        count > record["initial"]["changes_counts"].get(app, 0)
        for app, count in record["before_save"]["changes_counts"].items()
    )
    return record


def restore_probe(root, output, source, expected_sha):
    import os

    if not expected_sha or sha256(source / "receipt.json") != expected_sha:
        raise ValueError("Untrusted live receipt")
    live = read_json(source / "receipt.json")
    if (
        live.get("task_id") != TASK
        or live.get("evaluation", {}).get("success") is not True
    ):
        raise ValueError("Live success not established; stop this object")
    export = source / "export"
    verify_export(export, live["exported"]["files"])
    environment = environment_check()
    experiment = "stage2b_restore_" + digest(str(output.resolve()))[:20]
    adapter = AppWorldAdapter(root, TASK, experiment)
    record = {
        "pid": os.getpid(),
        "environment": environment,
        "experiment": experiment,
        "live_receipt_sha256": expected_sha,
    }
    try:
        record["initial"] = state_observation(adapter.world)
        adapter.import_state(export, "stage2b_import")
        record["after_import"] = state_observation(adapter.world)
        adapter.save_terminal()
        terminal = Path(adapter.world.output_db_home_path_on_disk)
        record["saved_terminal"] = disk_observation(terminal)
        write_json(output / "boundaries.json", record)
    finally:
        adapter.close()
    record["evaluation"] = evaluate_isolated(TASK, experiment, root, output)
    write_json(output / "official_tracker.json", record["evaluation"].get("official"))
    record["evaluation_db_unchanged"] = (
        files_manifest(terminal) == record["saved_terminal"]["files"]
    )
    record["export_unchanged"] = files_manifest(export) == live["exported"]["files"]
    live["receipt_sha256"] = expected_sha  # Parent file hash, never stored into itself.
    record["snapshot_scoring_integrity"] = integrity_verdict(live, record)
    return record


def run_recorded(output, operation):
    """Keep failed/incomplete attempts; a retry must use a different directory."""
    output.mkdir(parents=True, exist_ok=False)
    start = CLOCK[0]()
    record = {
        "revision": REVISION,
        "task_id": TASK,
        "model_requests": 0,
        "script_sha256": sha256(__file__),
    }
    try:
        record.update(operation())
        record["execution_status"] = "completed"
    except Exception as exc:
        record.update(execution_status="error", exception_type=type(exc).__name__)
        raise
    finally:
        record["seconds"] = CLOCK[0]() - start
        write_json(output / "receipt.json", record)
        write_json(output / "manifest.json", {"files": files_manifest(output)})
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["uri", "live", "restore"])
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--root", type=Path, default=Path("data/appworld"))
    p.add_argument("--source", type=Path)
    p.add_argument("--source-sha")
    a = p.parse_args()
    result = run_recorded(
        a.output,
        lambda: (
            uri_probe(a.output)
            if a.mode == "uri"
            else live_probe(a.root, a.output)
            if a.mode == "live"
            else restore_probe(a.root, a.output, a.source, a.source_sha)
        ),
    )
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("execution_status", "seconds", "snapshot_scoring_integrity")
                if k in result
            }
        )
    )


if __name__ == "__main__":
    main()
