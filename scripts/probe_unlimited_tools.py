"""Real AppWorld replay/limit probes, explicitly not new Teacher demonstrations."""

import argparse
import copy
import json
import sys
import uuid
from pathlib import Path

from recoveragent.adapter import AppWorldAdapter
from recoveragent.io import digest, files_manifest, read_json, write_json
from recoveragent.runner import now
from recoveragent.teacher import normalize_message
from recoveragent.teacher_history import capture_terminal, validate_history
from recoveragent.tool_modes import execute_with_mode


def probe(source, output, through_turn=8, read_executions=101):
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    original_files = files_manifest(source)
    trajectory = read_json(source / "trajectory.json")
    config = read_json(source.parent / (source.name + ".config.json"))
    task = trajectory["task_id"]
    messages = copy.deepcopy(trajectory["messages"][:2])
    adapter = AppWorldAdapter(
        config["appworld_root"],
        task,
        "probe_replay_" + uuid.uuid4().hex[:12],
        seed=config["environment_seed"],
        max_interactions=sys.maxsize,
    )
    started = now()
    count, known_feedback, rows = 0, 0, []
    try:
        if adapter.fingerprint() != trajectory["initial_state_hash"]:
            raise ValueError("Replay does not start at the same state")
        tools = adapter.initial_tools()
        for event in trajectory["turns"][:through_turn]:
            if digest(messages) != event["history_hash"] or tools != event["tools"]:
                raise ValueError("State/history/schema replay diverged before request")
            response = read_json(
                source / "requests" / f"{event['request_id']}.response.json"
            )
            message = normalize_message(response, max_calls=None)
            messages.append(message)
            feedback = []
            for offset, call in enumerate(message.get("tool_calls", [])):
                if now() - started > 180:
                    raise TimeoutError("Replay diagnostic time budget exceeded")
                function = call["function"]
                result, tools = execute_with_mode(
                    adapter,
                    function["name"],
                    function["arguments"],
                    tools,
                    "dynamic_discovery",
                )
                if "Execution failed" in result:
                    raise ValueError("Real replay tool execution failed")
                observation = {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": function["name"],
                    "content": result,
                }
                if event.get("tool_results"):
                    if observation != event["tool_results"][offset]:
                        raise ValueError("Previously recorded observation changed")
                    known_feedback += 1
                messages.append(observation)
                feedback.append(observation)
                count += 1
            validate_history(messages)
            write_json(
                output / "replay" / f"{event['request_id']}.json",
                {
                    "messages": messages,
                    "feedback": feedback,
                    "source_request_id": event["request_id"],
                    "source_was_rejected": "format_error" in event,
                },
            )
            rows.append(
                {
                    "source_request_id": event["request_id"],
                    "source_dispatched": event.get("dispatched_count", 0),
                    "replay_dispatched": len(feedback),
                    "total_executed": count,
                }
            )
        checkpoint = capture_terminal(
            adapter,
            output / "terminal",
            messages,
            tools,
            {
                "purpose": "cached_actor_replay_diagnostic_only",
                "new_teacher_demonstration": False,
                "source": str(source),
            },
        )
    finally:
        adapter.close()
    # Separate world: prove the adapter's old default of 100 is no longer active.
    adapter = AppWorldAdapter(
        config["appworld_root"],
        task,
        "probe_count_" + uuid.uuid4().hex[:12],
        seed=config["environment_seed"],
        max_interactions=sys.maxsize,
    )
    try:
        tools = adapter.initial_tools()
        before = adapter.fingerprint()
        receipts = []
        for index in range(read_executions):
            if now() - started > 180:
                raise TimeoutError("Diagnostic time budget exceeded")
            result, tools = adapter.execute("supervisor__show_active_task", "{}", tools)
            if "Execution failed" in result:
                raise ValueError("Real count probe execution failed")
            receipts.append({"index": index + 1, "observation_sha256": digest(result)})
        unchanged_state = before == adapter.fingerprint()
        write_json(output / "count_probe_receipts.json", receipts)
        observed_interactions = adapter.world.num_interactions
    finally:
        adapter.close()
    if files_manifest(source) != original_files:
        raise ValueError("Original source evidence changed")
    report = {
        "status": "PASS",
        "source": str(source),
        "output": str(output),
        "source_files_hash": digest(original_files),
        "task_id": task,
        "new_model_requests": 0,
        "new_teacher_demonstration": False,
        "hidden_evaluator_read": False,
        "training_started": False,
        "historical_observations_matched": known_feedback,
        "replay_rows": rows,
        "replayed_tool_calls": count,
        "terminal_checkpoint_id": checkpoint,
        "count_probe_calls": read_executions,
        "observed_interactions": observed_interactions,
        "count_probe_logical_state_unchanged": unchanged_state,
        "appworld_integer_sentinel": sys.maxsize,
        "wall_seconds": now() - started,
    }
    write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--output", required=True)
    parser.add_argument("--through-turn", type=int, default=8)
    parser.add_argument("--read-executions", type=int, default=101)
    args = parser.parse_args()
    print(
        json.dumps(
            probe(args.source, args.output, args.through_turn, args.read_executions)
        )
    )


if __name__ == "__main__":
    main()
