"""Real AppWorld state/restore probes with SCRIPTED outputs, never model scores."""

import argparse
import faulthandler
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from recoveragent import student
from recoveragent.adapter import AppWorldAdapter
from recoveragent.evaluation import environment_check, evaluate_isolated
from recoveragent.harness import RULES, SCHEMA_REVISION
from recoveragent.io import read_json, write_json
from recoveragent.p3c import drive, restore_probe, classify_episode
from recoveragent.state import inspect_snapshot
from recoveragent.student_evaluation import execution_clock
from review_harness_probe import ScriptedTokenizer, generation, block


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default="data/appworld")
    p.add_argument("--output", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--restore")
    a = p.parse_args()
    out = Path(a.output)
    faulthandler.dump_traceback_later(60)
    if a.restore:
        restore_probe(a.root, a.restore, a.namespace, a.output)
        faulthandler.cancel_dump_traceback_later()
        return
    out.mkdir(parents=True, exist_ok=False)
    environment = environment_check()
    task = "e3d6c94_3"
    # Evaluate a separate unmodified initial state first. No hidden results enter
    # the subsequent scripted Actor context.
    adapter = AppWorldAdapter(a.root, task, a.namespace + "_evaluation")
    adapter.save_terminal()
    adapter.close()
    evaluation = evaluate_isolated(task, a.namespace + "_evaluation", a.root, out)
    assert evaluation["category"] == "task_failure" and evaluation["success"] is False
    adapter = AppWorldAdapter(a.root, task, a.namespace + "_source")
    try:
        initial = adapter.fingerprint()
        tools = [
            t
            for t in adapter.initial_tools()
            if t["function"]["name"]
            in {
                "api_docs__show_api_doc",
                "api_docs__show_api_descriptions",
                "supervisor__show_account_passwords",
            }
        ]
        with patch.object(
            student,
            "TEMPLATE_SHA256",
            hashlib.sha256(ScriptedTokenizer.chat_template.encode()).hexdigest(),
        ):
            s = student.StudentSession(
                adapter,
                ScriptedTokenizer(),
                [
                    {"role": "system", "content": RULES},
                    {"role": "user", "content": json.dumps(adapter.public_task())},
                ],
                tools,
                tool_mode="dynamic_discovery",
                output=out / "interaction",
                checkpoint_id="SCRIPTED_NOT_MODEL",
                protocol_harness=True,
                harness_revision=SCHEMA_REVISION,
                clock=execution_clock,
            )
        call_count = 0
        before_partial = None
        real_execute = adapter.execute

        def execute(name, arguments, visible):
            nonlocal call_count
            call_count += 1
            result = real_execute(name, arguments, visible)
            if (
                name == "simple_note__create_note"
                and json.loads(arguments)["title"] == "probe_lost_result"
            ):
                raise OSError("SCRIPTED loss after real public write")
            return result

        adapter.execute = execute

        def scripted(request):
            nonlocal before_partial
            number = len(s.events)
            if number == 1:
                text = block("api_docs__show_api_descriptions", {})
            elif number == 2:
                text = "".join(
                    block(
                        "api_docs__show_api_doc",
                        {"app_name": "simple_note", "api_name": api},
                    )
                    for api in ["login", "create_note"]
                )
                text += block("supervisor__show_account_passwords", {})
            elif number == 3:
                passwords = json.loads(s.messages[-1]["content"])
                password = next(
                    r["password"]
                    for r in passwords
                    if r["account_name"] == "simple_note"
                )
                text = block(
                    "simple_note__login",
                    {
                        "username": adapter.public_task()["supervisor"]["email"],
                        "password": password,
                    },
                )
            else:
                token = json.loads(
                    next(
                        m["content"]
                        for m in s.messages
                        if m.get("name") == "simple_note__login"
                    )
                )["access_token"]
                args = {
                    "access_token": token,
                    "title": "probe_kept_a",
                    "content": "SCRIPTED state probe",
                }
                if number == 4:
                    text = block("simple_note__create_note", args) + block(
                        "simple_note__create_note", {**args, "title": "probe_kept_b"}
                    )
                elif number == 5:
                    text = block("simple_note__create_note", args) + block(
                        "simple_note__create_note", {**args, "not_in_schema": True}
                    )
                elif number == 6:
                    before_partial = adapter.fingerprint()
                    text = block(
                        "simple_note__create_note",
                        {**args, "title": "probe_partial_first"},
                    ) + block(
                        "simple_note__create_note",
                        {**args, "title": "probe_lost_result"},
                    )
                else:
                    raise AssertionError("Unexpected scripted step")
            return generation(text)(request)

        collected = drive(
            s, scripted, out, {"synthetic_probe": True, "model_requests": 0}
        )
        assert s.termination == "interrupted_batch", s.termination
        assert collected["fault"]["category"] == "worker_environment"
        assert (
            len(s.events) == 6
            and s.events[0]["protocol_rejection"] == "invalid_arguments"
        )
        assert (
            "app_name"
            in json.loads(s.events[0]["feedback"]["content"])["schema_error"][
                "missing_fields"
            ]
        )
        assert s.events[4]["appworld_dispatched_count"] == 0
        final = adapter.fingerprint()
        assert initial != before_partial != final and not adapter.world.task_completed()
        interrupted = read_json(out / "interaction/interruption/manifest.json")
        assert (
            interrupted["logical_state_hash"] == final
            and interrupted["resume_eligible"] is False
        )
        assert (
            s.events[-1]["dispatched_count"] == 1
            and s.events[-1]["appworld_attempted_count"] == 2
        )
        assert not classify_episode(evaluation, collected["fault"], s.termination)[
            "natural_failure"
        ]
        active = next(
            b for b in collected["boundaries"] if b["snapshot_ref"].endswith("004_post")
        )
        assert (
            inspect_snapshot(active["snapshot_ref"])[0]["logical_state_hash"]
            == before_partial
        )
        write_json(out / "collected.json", collected)
    finally:
        adapter.close()
    probes = []
    for index in range(2):
        dest = out / f"restore_{index}.json"
        with (out / f"restore_{index}.log").open("x") as log:
            subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--root",
                    a.root,
                    "--output",
                    str(dest),
                    "--namespace",
                    a.namespace + f"_restore_{index}",
                    "--restore",
                    active["snapshot_ref"],
                ],
                check=True,
                timeout=120,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        probes.append(read_json(dest))
    assert len({r["pid"] for r in probes} | {os.getpid()}) == 3
    assert (
        inspect_snapshot(active["snapshot_ref"])[0]["logical_state_hash"]
        == before_partial
    )
    summary = {
        "status": "PASS",
        "scope": "Scripted outputs on real AppWorld, not Teacher/Student performance",
        "real_model_requests": 0,
        "natural_failures": 0,
        "training_steps": 0,
        "environment": environment,
        "real_public_tool_calls": call_count,
        "returned_results": call_count - 1,
        "complete_checkpoints": len(collected["boundaries"]),
        "state_write_and_restore": True,
        "independent_process_restores": probes,
        "partial_write_saved_but_not_resumable": True,
        "mixed_invalid_batch_zero_calls": True,
        "schema_specific_feedback": True,
        "official_evaluation_category": evaluation["category"],
        "official_success": evaluation["success"],
        "exception_types": evaluation["exception_types"],
    }
    write_json(out / "summary.json", summary)
    faulthandler.cancel_dump_traceback_later()
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
