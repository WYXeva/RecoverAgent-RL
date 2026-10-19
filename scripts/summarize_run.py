"""Recompute public-safe P0–P2 evidence from local raw trajectories and evaluations."""

import argparse
from pathlib import Path

from recoveragent.io import read_json, write_json
from recoveragent.state import inspect_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.run)
    rows = []
    for path in sorted(root.glob("*/trajectory.json")):
        trajectory = read_json(path)
        events = trajectory["events"]
        generated = sum(x["generation"]["generated_tokens"] for x in events)
        prompt = sum(x["generation"]["prompt_tokens"] for x in events)
        dispatched = sum(
            "tool_result" in x and "validation_error" not in x for x in events
        )
        if trajectory["status"] == "valid":
            assert generated == trajectory["usage"]["generated_tokens"]
            assert prompt == trajectory["usage"]["prompt_tokens"]
            assert dispatched == trajectory["usage"]["dispatched_tool_calls"]
            evaluation = read_json(trajectory["evaluation_ref"])
            assert trajectory["reward"] == int(evaluation["success"])
            inspect_snapshot(trajectory["snapshot_ref"])
        rows.append(
            {
                "trajectory_id": trajectory["trajectory_id"],
                "mode": trajectory["mode"],
                "task_id": trajectory["task_id"],
                "status": trajectory["status"],
                "seed": trajectory["seed"],
                "worker_pid": trajectory["worker_pid"],
                "reward": trajectory["reward"],
                "termination_reason": trajectory["termination_reason"],
                "usage": trajectory["usage"],
                "start": trajectory.get("start"),
                "evidence": str(path),
            }
        )
    group_path = root / "recovery_group.json"
    group = read_json(group_path) if group_path.exists() else {}
    public = {
        "source_run": str(root),
        "summary": read_json(root / "summary.json"),
        "policy": read_json(root / "config.json")["policy_identity"],
        "trajectories": rows,
        "group": {k: v for k, v in group.items() if k != "branches"},
        "raw_task_content_included": False,
        "recomputed_from_raw_events": True,
    }
    write_json(args.output, public)
    print(args.output)


if __name__ == "__main__":
    main()
