"""Real AppWorld and Qwen tokenizer; synthetic L1/tool replies, ZERO model inference."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from probe_egtm import TokenizerPipe
from recoveragent.io import canonical, read_json, write_json
from recoveragent.memory_store import json_value


def probe(args):
    from recoveragent.adapter import AppWorldAdapter
    from recoveragent.evaluation import environment_check
    from recoveragent.p3c import save_boundary, verify_source
    from recoveragent.state import inspect_snapshot
    from recoveragent.student import StudentSession
    from recoveragent.student_backend import validate_request
    from recoveragent.student_evaluation import evaluation_prompt, execution_clock
    from recoveragent.task_memory import MODEL_RUNTIME

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    config = read_json("configs/egtm_v2_p3c.json")
    source_lock = verify_source(MODEL_RUNTIME)
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
                memory_config=config["memory"],
            )
            snapshots, token_rows = [], []

            def step(calls):
                def generate(request):
                    validate_request(request, session.identity, tokenizer)
                    if request.get("purpose") == "memory_update":
                        # This fixture tests acceptance/rollback, NOT semantic ability.
                        claim = {
                            "text": "Scripted note-state probe; official task completion is unknown.",
                            "status": "unverified",
                            "evidence_ids": [],
                        }
                        value = {
                            "goal": "Scripted infrastructure probe",
                            "constraints": ["Keep original task verbatim"],
                            "progress": claim,
                            "completed": [],
                            "pending": [claim],
                            "important_facts": [],
                            "uncertainties": [claim],
                            "errors": [],
                            "need_evidence": [],
                        }
                        rows = [
                            r
                            for r in session.memory.index.rows
                            if r["tool_name"] == "simple_note__show_note"
                            and isinstance(json_value(r["content"]), dict)
                            and "content" in json.loads(r["content"])
                        ]
                        if rows:
                            row = rows[-1]
                            value["important_facts"] = [
                                {
                                    "evidence_ids": [row["source_id"]],
                                    "path": ["content"],
                                    "value": json.loads(row["content"])["content"],
                                    "status": "stale"
                                    if row["source_id"] in session.memory.stale_sources
                                    else "observed",
                                }
                            ]
                            value["need_evidence"] = [{"source_id": row["source_id"]}]
                        text = (
                            "{bad json"
                            if not session.memory.updates
                            else canonical(value)
                        )
                    else:
                        token_rows.append(request["memory_compilation"])
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
                        **(
                            {"text": tokenizer.decode(ids, skip_special_tokens=False)}
                            if request.get("purpose")
                            else {}
                        ),
                    }

                while True:
                    event = session.step(generate)
                    if event.get("purpose") != "memory_update":
                        return event

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
                            "title": "EGTM v2 script",
                            "content": "Version one",
                        },
                    )
                ]
            )
            note = json.loads(session.messages[-1]["content"])["note_id"]
            step([("simple_note__show_note", {"access_token": token, "note_id": note})])
            assert adapter.fingerprint() != initial_db and session.memory.l1 is None
            snapshots.append(
                save_boundary(
                    session,
                    output / "pre_error",
                    "pre_action",
                    {"scripted_probe": True},
                )
            )
            error = step(
                [("simple_note__show_note", {"access_token": token, "note_id": -1})]
            )
            assert error["failure_category"] == "business"
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
            step([("simple_note__show_note", {"access_token": token, "note_id": note})])
            assert (
                session.memory.l1
                and session.memory.l1["important_facts"][0]["status"] == "stale"
            )
            snapshots.append(
                save_boundary(
                    session,
                    output / "post_error",
                    "post_action",
                    {"scripted_probe": True},
                )
            )
            for _ in range(16):
                step([("api_docs__show_api_descriptions", {"app_name": "amazon"})])
            assert (
                session.usage["compressed_requests"] > 0
                and session.usage["saved_input_tokens"] > 0
            )
            snapshots.append(
                save_boundary(
                    session,
                    output / "late",
                    "pre_termination",
                    {"scripted_probe": True},
                )
            )
            before, db = session.memory.identity, adapter.fingerprint()
            execute = adapter.execute

            def lose_return(name, arguments, tools):
                result = execute(name, arguments, tools)
                if (
                    name == "simple_note__create_note"
                    and json.loads(arguments)["title"] == "lost return"
                ):
                    raise OSError("SCRIPTED loss after actual side effect")
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
                                "content": "partial",
                            },
                        )
                        for title in ("returned", "lost return")
                    ]
                )
            except OSError:
                pass
            else:
                raise AssertionError("Expected interrupted batch")
            assert session.memory.identity == before and adapter.fingerprint() != db
            assert (
                session.events[-1]["dispatched_count"] == 1
                and session.events[-1]["inflight_call_id"]
            )
            try:
                save_boundary(session, output / "forbidden_partial", "post_action", {})
            except ValueError:
                pass
            else:
                raise AssertionError("Partial batch became a checkpoint")
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
                    "scripts/probe_egtm.py",
                    "--restore",
                    snapshot["snapshot_ref"],
                    "--root",
                    args.root,
                    "--namespace",
                    args.namespace + f"_restore_{i}",
                    "--output",
                    str(destination),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=180,
            )
        result = read_json(destination)
        assert result["memory_matches"] and result["pid"] != os.getpid()
        assert (
            inspect_snapshot(snapshot["snapshot_ref"])[0]["checkpoint_id"]
            == snapshot["checkpoint_id"]
        )
        restorations.append(result)
    summary = {
        "status": "PASS",
        "source_lock_sha256": source_lock,
        "environment": environment,
        "scope": "Real AppWorld + pinned tokenizer; synthetic L1 and actions ONLY",
        "real_model_requests": 0,
        "weights_loaded": False,
        "training_started": False,
        "task_id": "e3d6c94_3",
        "usage": usage,
        "checkpoints": len(snapshots),
        "restorations": restorations,
        "partial_batch_rejected": True,
        "token_measurements": [
            {k: r[k] for k in ("original_tokens", "compiled_tokens", "compressed")}
            for r in token_rows
        ],
        "wall_seconds": execution_clock() - started,
    }
    write_json(output / "summary.json", summary)
    print(
        canonical({k: summary[k] for k in ("status", "scope", "usage", "wall_seconds")})
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data/appworld")
    parser.add_argument("--output", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--tokenizer-python", default=".venv-inference/bin/python")
    probe(parser.parse_args())
