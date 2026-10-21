"""Real AppWorld + real Qwen tokenizer, scripted generations; NOT model evaluation.

Two existing Python environments communicate over local JSON lines so no packages
or weights need installing. Only public API documentation/profile reads execute.
"""

import argparse
import contextlib
import faulthandler
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from types import SimpleNamespace


def worker(args):
    from recoveragent.adapter import AppWorldAdapter

    wire = sys.stdout
    faulthandler.dump_traceback_later(30, file=sys.stderr)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            adapter = AppWorldAdapter(args.root, args.task_id, args.namespace)
    finally:
        faulthandler.cancel_dump_traceback_later()
    wire.write(
        json.dumps(
            {
                "catalogue": adapter.catalogue,
                "tools": adapter.initial_tools(),
                "task": adapter.public_task(),
            }
        )
        + "\n"
    )
    wire.flush()
    try:
        for line in sys.stdin:
            request = json.loads(line)
            if request["op"] == "close":
                break
            with contextlib.redirect_stdout(sys.stderr):
                result, tools = adapter.execute(
                    request["name"], request["arguments"], request["tools"]
                )
                completed = adapter.world.task_completed()
            wire.write(
                json.dumps({"result": result, "tools": tools, "completed": completed})
                + "\n"
            )
            wire.flush()
    finally:
        with contextlib.redirect_stdout(sys.stderr):
            adapter.world.close()


def read_response(process):
    with selectors.DefaultSelector() as poll:
        poll.register(process.stdout, selectors.EVENT_READ)
        if not poll.select(60):
            raise TimeoutError("AppWorld protocol probe timed out")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("AppWorld protocol worker exited; inspect worker.log")
    return json.loads(line)


def probe(args):
    from recoveragent.base_sft import load_tokenizer
    from recoveragent.io import canonical, digest, write_json
    from recoveragent.student import StudentSession
    from recoveragent.teacher_history import validate_history

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    tokenizer, rows = load_tokenizer(), []
    for mode in ["dynamic_discovery", "api_predictor"]:
        folder = output / mode
        folder.mkdir()
        with (folder / "worker.log").open("x") as log:
            process = subprocess.Popen(
                [
                    args.appworld_python,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--root",
                    args.root,
                    "--task-id",
                    args.task_id,
                    "--namespace",
                    args.namespace + "_" + mode,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                env=dict(os.environ, PYTHONPATH="src"),
            )
            try:
                initial = read_response(process)

                def execute(name, arguments, tools):
                    process.stdin.write(
                        json.dumps(
                            {
                                "op": "execute",
                                "name": name,
                                "arguments": arguments,
                                "tools": tools,
                            }
                        )
                        + "\n"
                    )
                    process.stdin.flush()
                    reply = read_response(process)
                    assert "Execution failed" not in reply["result"]
                    return reply["result"], reply["tools"]

                adapter = SimpleNamespace(
                    catalogue=initial["catalogue"],
                    execute=execute,
                    world=SimpleNamespace(task_completed=lambda: False),
                )
                session = StudentSession(
                    adapter,
                    tokenizer,
                    [
                        {
                            "role": "user",
                            "content": "Protocol probe: public documentation and profile reads only.",
                        }
                    ],
                    initial["tools"],
                    tool_mode=mode,
                    output=folder / "session",
                )
                before = digest(session.tools)
                app = sorted(
                    n
                    for n in initial["task"]["app_descriptions"]
                    if n not in {"api_docs", "supervisor", "admin"}
                )[0]

                def scripted(calls):
                    def generate(request):
                        text = "\n".join(
                            "<tool_call>\n"
                            + canonical({"name": n, "arguments": a})
                            + "\n</tool_call>"
                            for n, a in calls
                        )
                        ids = tokenizer(text, add_special_tokens=False)["input_ids"] + [
                            tokenizer.eos_token_id
                        ]
                        return {
                            "policy": request["policy"],
                            "input_token_ids": request["input_token_ids"],
                            "token_ids": ids,
                            "finish_reason": "eos",
                            "synthetic_generation": True,
                        }

                    return generate

                session.step(
                    scripted(
                        [
                            ("api_docs__show_api_descriptions", {"app_name": app}),
                            ("supervisor__show_profile", {}),
                        ]
                    )
                )
                # These names are read from the real previous observation. No
                # placeholder substitution or future lookup occurs within a batch.
                observation = session.messages[-2]
                assert observation["name"] == "api_docs__show_api_descriptions"
                selected = [
                    row["name"] for row in json.loads(observation["content"])[:2]
                ]
                assert len(selected) == 2
                session.step(
                    scripted(
                        [
                            (
                                "api_docs__show_api_doc",
                                {"app_name": app, "api_name": api},
                            )
                            for api in selected
                        ]
                    )
                )
                next_request = session.build_request()
                new_names = {t["function"]["name"] for t in next_request["tools"]}
                if mode == "dynamic_discovery":
                    assert all(app + "__" + n in new_names for n in selected)
                    assert digest(session.tools) != before
                else:
                    assert digest(session.tools) == before
                validate_history(session.messages)
                assert next_request["messages"] == session.messages
                assert [e["dispatched_count"] for e in session.events] == [2, 2]
                rows.append(
                    {
                        "tool_mode": mode,
                        "real_tool_calls": session.usage["tool_calls"],
                        "synthetic_generation_turns": session.usage["model_requests"],
                        "real_feedback_hash": digest(
                            [m for m in session.messages if m["role"] == "tool"]
                        ),
                        "next_request_hash": digest(next_request),
                        "complete_history": True,
                        "actual_prior_result_used": True,
                        "schema_transition_checked": True,
                    }
                )
            finally:
                if process.poll() is None:
                    process.stdin.write('{"op":"close"}\n')
                    process.stdin.flush()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            if process.returncode != 0:
                raise RuntimeError("AppWorld protocol worker failed")
    report = {
        "status": "PASS",
        "scope": "real AppWorld and tokenizer; scripted completions, not autonomous Student performance or Teacher data",
        "task_id": args.task_id,
        "rows": rows,
        "real_tool_calls": sum(r["real_tool_calls"] for r in rows),
        "model_requests": 0,
        "model_weights_loaded": False,
        "training_started": False,
        "teacher_demonstrations_generated": 0,
        "official_task_evaluation": "NOT_RUN",
    }
    write_json(output / "summary.json", report)
    write_json(args.report, report)
    print("PASS: 8 real public-tool calls; 0 model requests or training steps")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--root", default="data/appworld")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--appworld-python", default=".venv/bin/python")
    parser.add_argument("--output")
    parser.add_argument("--report")
    args = parser.parse_args()
    worker(args) if args.worker else probe(args)


if __name__ == "__main__":
    main()
