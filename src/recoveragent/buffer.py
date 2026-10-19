"""Persistent, train-only failure replay index; raw evidence is never evicted."""

import random
from pathlib import Path

from .io import digest, read_json, write_json
from .state import inspect_snapshot


class FailureBuffer:
    def __init__(self, root, capacity=100):
        if capacity < 1:
            raise ValueError("Buffer capacity must be positive")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity

    def add(self, trajectory, snapshot):
        manifest, _, _ = inspect_snapshot(snapshot)
        evaluation = read_json(trajectory["evaluation_ref"])
        if (
            trajectory["split"] != "train"
            or trajectory["task_id"] != manifest["task_id"]
        ):
            raise ValueError("Failure provenance mismatch")
        if trajectory["status"] != "valid" or evaluation["success"] is not False:
            raise ValueError(
                "Only officially failed valid trajectories enter the buffer"
            )
        if (
            evaluation["task_id"] != trajectory["task_id"]
            or evaluation["trajectory_id"] != trajectory["trajectory_id"]
        ):
            raise ValueError("Evaluation provenance mismatch")
        if not trajectory["natural"]:
            raise ValueError("Synthetic trajectories are not natural failures")
        identity = digest(
            {"checkpoint": manifest["checkpoint_id"], "policy": trajectory["policy"]}
        )
        record = {
            "failure_id": identity,
            "source_trajectory_id": trajectory["trajectory_id"],
            "task_id": trajectory["task_id"],
            "split": "train",
            "scenario_id": trajectory["scenario_id"],
            "source_policy": trajectory["policy"],
            "snapshot_ref": str(Path(snapshot).resolve()),
            "checkpoint_id": manifest["checkpoint_id"],
            "evaluation_ref": trajectory["evaluation_ref"],
            "failure_type": trajectory["termination_reason"],
            "candidate_reason": "Official final failure; no root cause inferred",
            "recoverability_status": "unknown",
            "stats": {},
        }
        path = self.root / (identity + ".json")
        if not path.exists():
            write_json(path, record)
        return record

    def records(self):
        paths = sorted(
            self.root.glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name)
        )
        records = []
        for path in paths[-self.capacity :]:
            record = read_json(path)
            manifest, _, _ = inspect_snapshot(record["snapshot_ref"])
            if (
                record["split"] != "train"
                or manifest["checkpoint_id"] != record["checkpoint_id"]
            ):
                raise ValueError("Invalid replay entry; preserve and investigate")
            records.append(record)
        return records

    def sample(self, seed=42, recent_first=True):
        records = self.records()
        if not records:
            raise ValueError("Empty failure buffer")
        return records[-1] if recent_first else random.Random(seed).choice(records)
