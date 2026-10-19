"""Real AppWorld state test, deliberately scripted; never a natural-failure record."""

import argparse
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys

from recoveragent.adapter import AppWorldAdapter
from recoveragent.io import digest, read_json, write_json
from recoveragent.state import capture, inspect_snapshot, restore


def main():
    faulthandler.dump_traceback_later(90)
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data/appworld")
    parser.add_argument("--output", required=True)
    parser.add_argument("--branch", type=int)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    from appworld import load_task_ids, update_root

    update_root(str(Path(args.root).resolve()))
    task = load_task_ids("train")[0]
    if args.branch is None:
        output.mkdir(parents=True, exist_ok=False)
        adapter = AppWorldAdapter(args.root, task, output.name + "_source")
        baseline = adapter.fingerprint()
        tools = adapter.initial_tools()
        messages = [{"role": "user", "content": json.dumps(adapter.public_task())}]
        result, _ = adapter.execute(
            "supervisor__complete_task", {"answer": "state-probe-A"}, tools
        )
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "probe_A",
                            "type": "function",
                            "function": {
                                "name": "supervisor__complete_task",
                                "arguments": '{"answer":"state-probe-A"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "probe_A", "content": result},
            ]
        )
        saved = adapter.fingerprint()
        assert baseline != saved, "The public API must really modify a database"
        source = capture(
            adapter,
            output / "source",
            messages,
            tools,
            "post_action",
            {"synthetic_probe": True},
        )
        adapter.execute("supervisor__complete_task", {"answer": "state-probe-B"}, tools)
        assert adapter.fingerprint() != saved
        restore(adapter, source)
        assert adapter.fingerprint() == saved
        assert adapter.world.save_state() is None
        adapter.close()
        write_json(
            output / "same_worker.json",
            {
                "baseline": baseline,
                "saved": saved,
                "write_restore": True,
                "noarg_save_returned_none": True,
            },
        )
        faulthandler.cancel_dump_traceback_later()
        for branch in range(4):
            subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--root",
                    args.root,
                    "--output",
                    str(output),
                    "--branch",
                    str(branch),
                ],
                check=True,
                timeout=120,
            )
        branches = [read_json(output / f"branch_{i}.json") for i in range(4)]
        assert len({x["pid"] for x in branches}) == 4
        assert all(
            x["start_hash"] == saved and x["restored_again_hash"] == saved
            for x in branches
        )
        assert len({x["mutated_hash"] for x in branches}) == 4
        inspect_snapshot(source)
        write_json(
            output / "summary.json",
            {
                "status": "PASS",
                "real_appworld": True,
                "natural_failure": False,
                "task_id": task,
                "branches": branches,
                "source_unchanged": True,
                "original_reference_evaluation": True,
            },
        )
        print("Real AppWorld write/save/restore + four process isolation: PASS")
    else:
        adapter = AppWorldAdapter(
            args.root, task, output.name + f"_branch_{args.branch}"
        )
        manifest, context = restore(adapter, output / "source")
        start = adapter.fingerprint()
        assert adapter.world.task.ground_truth is None
        adapter.execute(
            "supervisor__complete_task",
            {"answer": f"isolated-{args.branch}"},
            context["tools"],
        )
        mutated = adapter.fingerprint()
        assert mutated != start
        restore(adapter, output / "source")
        repeated = adapter.fingerprint()
        adapter.save_terminal()
        adapter.close()
        from appworld.evaluator import evaluate_task

        tracker = evaluate_task(
            task, experiment_name=output.name + f"_branch_{args.branch}"
        )
        write_json(
            output / f"branch_{args.branch}.json",
            {
                "pid": os.getpid(),
                "start_hash": start,
                "mutated_hash": mutated,
                "restored_again_hash": repeated,
                "source_id": manifest["checkpoint_id"],
                "history_hash": digest(context["messages"]),
                "official_success": tracker.success,
                "official_stats": tracker.to_dict(stats_only=True),
                "ground_truth_hidden_from_world": True,
            },
        )
    faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
