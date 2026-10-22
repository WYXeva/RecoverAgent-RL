"""Immutable historical replay in a new environment; never new Teacher samples.

Every replay and every official evaluation uses a separate process. Stop that
replay at the first public-observation divergence. No model, hidden answer or
automatic business repair is involved. New sidecar receipts do not edit v1.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from recoveragent.demonstrations import sha256
from recoveragent.io import read_json, write_json


def worker(source, root, namespace, output):
    from recoveragent.adapter import AppWorldAdapter
    from recoveragent.evaluation import environment_check, evaluate_isolated
    from recoveragent.tool_modes import execute_with_mode

    environment = environment_check()
    t = read_json(source)
    config = read_json(source.parent.parent / "config.json")
    result = {
        "trajectory_id": t["trajectory_id"],
        "task_id": t["task_id"],
        "source_sha256": sha256(source),
        "scope": "historical_action_replay",
        "namespace": namespace,
        "environment": environment,
        "model_requests": 0,
        "teacher_demonstrations_generated": 0,
        "real_tool_calls": 0,
        "observations_match": True,
        "status": "incomplete",
    }
    adapter = None
    try:
        adapter = AppWorldAdapter(
            root,
            t["task_id"],
            namespace,
            seed=config["environment_seed"],
            max_interactions=sys.maxsize,
        )
        result["initial_state_matches"] = (
            adapter.fingerprint() == t["initial_state_hash"]
        )
        if not result["initial_state_matches"]:
            raise ValueError("Initial logical state mismatch")
        tools = t["initial_tools"]
        for turn in t["turns"]:
            if turn["tools"] != tools:
                raise ValueError("Replay tool schema boundary mismatch")
            idx = turn["assistant_index"]
            calls = t["messages"][idx].get("tool_calls", [])
            for offset, call in enumerate(calls):
                verdicts = turn.get("harness_verdicts", [None] * len(calls))
                if verdicts[offset] is not None:
                    continue
                f = call["function"]
                observation, tools = execute_with_mode(
                    adapter,
                    f["name"],
                    f["arguments"],
                    tools,
                    t.get("tool_mode", "dynamic_discovery"),
                )
                result["real_tool_calls"] += 1
                if observation != t["messages"][idx + 1 + offset]["content"]:
                    result.update(
                        observations_match=False,
                        divergence_call=result["real_tool_calls"],
                    )
                    raise ValueError("Public observation divergence")
        adapter.export_state("revalidation_terminal", output / "terminal_dbs")
        adapter.save_terminal()
        result["model_hash_entries"] = len(
            read_json(output / "terminal_dbs/model_hashes.json")
        )
        before = adapter.fingerprint()
        adapter.import_state(output / "terminal_dbs", "revalidation_roundtrip")
        result["snapshot_roundtrip_matches"] = adapter.fingerprint() == before
        adapter.close()
        adapter = None
        evaluated = evaluate_isolated(t["task_id"], namespace, root, output)
        result.update(
            status=evaluated["status"],
            official_success=evaluated["success"],
            evaluation_exception_types=evaluated["exception_types"],
        )
    except Exception as exc:
        result.update(status="replay_error", error_type=type(exc).__name__)
    finally:
        if adapter is not None:
            adapter.close()
        write_json(output / "receipt.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", default="data/base_sft_v1")
    p.add_argument("--root", default="data/appworld")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--source", type=Path)
    p.add_argument("--namespace")
    p.add_argument("--timeout", type=int, default=180)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    if a.source:
        worker(a.source, a.root, a.namespace, a.output)
        return
    dataset = Path(a.dataset)
    samples = [
        json.loads(line)
        for part in ("train", "validation")
        for line in (dataset / f"model_input/{part}.jsonl").read_text().splitlines()
    ]
    sources = {
        p.parent.name: p for p in Path("runs").glob("*/teacher_*/trajectory.json")
    }
    plan = [
        {
            "trajectory_id": s["trajectory_id"],
            "source": str(sources[s["trajectory_id"]]),
            "source_sha256": sha256(sources[s["trajectory_id"]]),
        }
        for s in samples
    ]
    write_json(
        a.output / "plan.json",
        {"dataset_manifest_sha256": sha256(dataset / "manifest.json"), "rows": plan},
    )
    rows, started = [], time.monotonic()
    for i, row in enumerate(plan):
        out = a.output / row["trajectory_id"]
        name = f"harness_revalidation_{a.output.name}_{i:03d}"
        with (a.output / f"{i:03d}.log").open("x") as log:
            try:
                subprocess.run(
                    [
                        sys.executable,
                        __file__,
                        "--source",
                        row["source"],
                        "--root",
                        a.root,
                        "--namespace",
                        name,
                        "--output",
                        str(out),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=a.timeout,
                    env=dict(os.environ),
                )
                receipt = read_json(out / "receipt.json")
            except (subprocess.SubprocessError, OSError) as exc:
                receipt = dict(
                    row, status="worker_error", error_type=type(exc).__name__
                )
        rows.append(receipt)
        print(
            json.dumps(
                {
                    "finished": i + 1,
                    "total": len(plan),
                    "status": receipt["status"],
                    "success": receipt.get("official_success"),
                }
            ),
            flush=True,
        )
    result = {
        "scope": "historical replay; not Teacher rollout or new training data",
        "dataset_manifest_sha256": sha256(dataset / "manifest.json"),
        "attempts": len(rows),
        "official_successes": sum(r.get("official_success") is True for r in rows),
        "real_tool_calls": sum(r.get("real_tool_calls", 0) for r in rows),
        "model_requests": 0,
        "training_steps": 0,
        "wall_seconds": time.monotonic() - started,
        "rows": rows,
    }
    write_json(a.output / "summary.json", result)


if __name__ == "__main__":
    main()
