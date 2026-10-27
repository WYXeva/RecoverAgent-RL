"""Real AppWorld + pinned Qwen tokenizer, scripted outputs only; no model/GPU."""

import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

from recoveragent.io import canonical, read_json, write_json


def tokenizer_worker():
    from recoveragent.base_sft import load_tokenizer

    tokenizer = load_tokenizer()
    print(
        canonical(
            {
                "chat_template": tokenizer.chat_template,
                "eos_token_id": tokenizer.eos_token_id,
            }
        ),
        flush=True,
    )
    for line in sys.stdin:
        row = json.loads(line)
        method = getattr(tokenizer, row["method"])
        result = method(*row["args"], **row["kwargs"])
        if row["method"] == "__call__":
            result = dict(result)
        print(canonical(result), flush=True)


class TokenizerPipe:
    """Reuse the installed tokenizer environment without changing AppWorld deps."""

    def __init__(self, python, log):
        self.process = subprocess.Popen(
            [python, __file__, "--tokenizer-worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            env=dict(os.environ, HF_HUB_OFFLINE="1", TOKENIZERS_PARALLELISM="false"),
        )
        for key, value in self.read().items():
            setattr(self, key, value)

    def read(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(60):
                raise TimeoutError("Tokenizer worker did not respond")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("Tokenizer worker exited; inspect its log")
        return json.loads(line)

    def rpc(self, method, *args, **kwargs):
        self.process.stdin.write(
            json.dumps(
                {"method": method, "args": args, "kwargs": kwargs},
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )
        self.process.stdin.flush()
        return self.read()

    def apply_chat_template(self, *args, **kwargs):
        return self.rpc("apply_chat_template", *args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self.rpc("__call__", *args, **kwargs)

    def decode(self, *args, **kwargs):
        return self.rpc("decode", *args, **kwargs)

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise


def probe(args):
    from recoveragent.adapter import AppWorldAdapter
    from recoveragent.evaluation import environment_check
    from recoveragent.p3c import save_boundary, verify_source
    from recoveragent.state import inspect_snapshot
    from recoveragent.student import StudentSession
    from recoveragent.student_backend import validate_request
    from recoveragent.student_evaluation import evaluation_prompt, execution_clock
    from recoveragent.task_memory import memory_config, RUNTIME

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    config = read_json("configs/egtm_v1_p3c.json")
    source_lock = verify_source(RUNTIME)
    started = execution_clock()
    with (output / "tokenizer.log").open("x") as log:
        tokenizer = TokenizerPipe(args.tokenizer_python, log)
        adapter = None
        try:
            environment = environment_check()
            adapter = AppWorldAdapter(
                args.root, "e3d6c94_3", args.namespace, max_interactions=100
            )
            session = StudentSession(
                adapter,
                tokenizer,
                [
                    {"role": "system", "content": evaluation_prompt(config)},
                    {"role": "user", "content": canonical(adapter.public_task())},
                ],
                adapter.initial_tools(),
                tool_mode="dynamic_discovery",
                output=output / "interaction",
                protocol_harness=True,
                answer_guard=True,
                clock=execution_clock,
                checkpoint_id="SCRIPTED_NOT_A_MODEL",
                memory_config=memory_config({"enabled": True}),
            )
            snapshots = []
            token_rows = []

            def step(calls):
                def generate(request):
                    validate_request(request, session.identity, tokenizer)
                    token_rows.append(
                        {
                            "request": session.usage["model_requests"],
                            **request["memory_compilation"],
                        }
                    )
                    text = "".join(
                        "<tool_call>"
                        + canonical({"name": name, "arguments": arguments})
                        + "</tool_call>"
                        for name, arguments in calls
                    )
                    ids = tokenizer(text, add_special_tokens=False)["input_ids"] + [
                        tokenizer.eos_token_id
                    ]
                    return {
                        "policy": request["policy"],
                        "input_token_ids": request["input_token_ids"],
                        "token_ids": ids,
                        "finish_reason": "eos",
                    }

                return session.step(generate)

            # Whole-batch rejection does not ingest a fake successful observation.
            step([("api_docs__show_api_descriptions", {})])
            assert not session.memory.index.rows and adapter.world.num_interactions == 0
            step(
                [
                    (
                        "api_docs__show_api_doc",
                        {"app_name": "simple_note", "api_name": name},
                    )
                    for name in ("login", "create_note", "show_note", "update_note")
                ]
                + [("supervisor__show_account_passwords", {})]
            )
            password = next(
                r["password"]
                for r in json.loads(session.messages[-1]["content"])
                if r["account_name"] == "simple_note"
            )
            step(
                [
                    (
                        "simple_note__login",
                        {
                            "username": adapter.public_task()["supervisor"]["email"],
                            "password": password,
                        },
                    )
                ]
            )
            token = json.loads(session.messages[-1]["content"])["access_token"]
            initial_db = adapter.fingerprint()
            step(
                [
                    (
                        "simple_note__create_note",
                        {
                            "access_token": token,
                            "title": "EGTM scripted state probe",
                            "content": "Version one",
                        },
                    )
                ]
            )
            note = json.loads(session.messages[-1]["content"])["note_id"]
            assert adapter.fingerprint() != initial_db
            step([("simple_note__show_note", {"access_token": token, "note_id": note})])
            read_id = session.memory.index.rows[-1]["source_id"]
            snapshots.append(
                save_boundary(
                    session,
                    output / "pre_error",
                    "pre_action",
                    {"scripted_probe": True},
                )
            )
            event = step(
                [("simple_note__show_note", {"access_token": token, "note_id": -1})]
            )
            assert event["failure_category"] == "business"
            snapshots.append(
                save_boundary(
                    session,
                    output / "post_error",
                    "post_action",
                    {"scripted_probe": True},
                )
            )
            step(
                [
                    (
                        "simple_note__update_note",
                        {
                            "access_token": token,
                            "note_id": note,
                            "content": "Version two",
                        },
                    )
                ]
            )
            assert all(
                f["status"] == "stale"
                for f in session.memory.facts
                if f["tool_call_id"] == read_id
            )
            step([("simple_note__show_note", {"access_token": token, "note_id": note})])
            assert (
                json.loads(session.messages[-1]["content"])["content"] == "Version two"
            )
            # Fixed repeated PUBLIC catalogue reads create long actual results.
            # This is an execution/token probe, not an Agent task or benchmark.
            for _ in range(16):
                step([("api_docs__show_api_descriptions", {"app_name": "amazon"})])
            request = session.build_request()
            validate_request(request, session.identity, tokenizer)
            token_rows.append(
                {"request": "next_unsent", **request["memory_compilation"]}
            )
            assert any(
                r["compressed"] and r["compiled_tokens"] < r["original_tokens"]
                for r in token_rows
            )
            assert all(
                r["compiled_tokens"] + r["reserve_tokens"] <= 32768 for r in token_rows
            )
            snapshots.append(
                save_boundary(
                    session,
                    output / "late",
                    "pre_termination",
                    {"scripted_probe": True},
                )
            )
            memory_before, db_before = session.memory.identity, adapter.fingerprint()
            execute = adapter.execute

            def lose_return(name, arguments, tools):
                result = execute(name, arguments, tools)
                if (
                    name == "simple_note__create_note"
                    and json.loads(arguments)["title"] == "lost return"
                ):
                    raise OSError("SCRIPTED result loss after a real write")
                return result

            adapter.execute = lose_return
            try:
                step(
                    [
                        (
                            "simple_note__create_note",
                            {
                                "access_token": token,
                                "title": title,
                                "content": "partial side effect",
                            },
                        )
                        for title in ("returned", "lost return")
                    ]
                )
            except OSError:
                pass
            else:
                raise AssertionError("Expected scripted lost return")
            assert (
                adapter.fingerprint() != db_before
                and session.memory.identity == memory_before
            )
            assert (
                session.events[-1]["dispatched_count"] == 1
                and session.events[-1]["inflight_call_id"]
            )
            try:
                save_boundary(session, output / "forbidden_partial", "post_action", {})
            except ValueError:
                pass
            else:
                raise AssertionError("Partial batch must not produce a checkpoint")
            assert not (output / "forbidden_partial").exists()
            usage = session.usage.copy()
            write_json(output / "token_observations.json", token_rows)
        finally:
            if adapter is not None:
                adapter.close()
            tokenizer.close()
    restorations = []
    for i, snapshot in enumerate(snapshots):
        destination = output / f"restore_{i}.json"
        with (output / f"restore_{i}.log").open("x") as log:
            subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--restore",
                    snapshot["snapshot_ref"],
                    "--root",
                    args.root,
                    "--namespace",
                    args.namespace + f"_restore_{i}",
                    "--output",
                    str(destination),
                ],
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=180,
            )
        row = read_json(destination)
        assert row["memory_matches"] and row["pid"] != os.getpid()
        assert (
            inspect_snapshot(snapshot["snapshot_ref"])[0]["checkpoint_id"]
            == snapshot["checkpoint_id"]
        )
        restorations.append(row)
    compact = [r for r in token_rows if r["compressed"]]
    summary = {
        "status": "PASS",
        "source_lock_sha256": source_lock,
        "scope": "real AppWorld + pinned Qwen tokenizer, SCRIPTED completions only",
        "task_id": "e3d6c94_3",
        "environment": environment,
        "real_model_requests": 0,
        "weights_loaded": False,
        "training_started": False,
        "actual_tool_call_intents": usage["tool_calls"],
        "scripted_requests": usage["model_requests"],
        "usage": usage,
        "checkpoints": len(snapshots),
        "restorations": restorations,
        "write_invalidation_pass": True,
        "partial_batch_rejected": True,
        "compiled_requests": len(compact),
        "token_measurements": [
            {
                k: r[k]
                for k in (
                    "request",
                    "original_tokens",
                    "compiled_tokens",
                    "reserve_tokens",
                    "compressed",
                )
            }
            for r in token_rows
        ],
        "wall_seconds": execution_clock() - started,
        "success_rate_or_model_benefit": "NOT_MEASURED",
        "official_task_evaluation": "NOT_RUN",
    }
    write_json(output / "summary.json", summary)
    if args.report:
        write_json(args.report, summary)
    print(
        canonical(
            {
                k: summary[k]
                for k in (
                    "status",
                    "actual_tool_call_intents",
                    "real_model_requests",
                    "compiled_requests",
                    "checkpoints",
                )
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer-worker", action="store_true")
    parser.add_argument("--tokenizer-python", default=".venv-inference/bin/python")
    parser.add_argument("--root", default="data/appworld")
    parser.add_argument("--output")
    parser.add_argument("--report")
    parser.add_argument("--namespace")
    parser.add_argument("--restore")
    args = parser.parse_args()
    if args.tokenizer_worker:
        tokenizer_worker()
    elif args.restore:
        from recoveragent.p3c import restore_probe

        restore_probe(args.root, args.restore, args.namespace, args.output)
    else:
        probe(args)


if __name__ == "__main__":
    main()
