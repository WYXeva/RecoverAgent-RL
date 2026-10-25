"""New Harness runtime on real AppWorld, scripted messages only; no model calls."""

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from recoveragent import student
from recoveragent.adapter import AppWorldAdapter
from recoveragent.evaluation import environment_check, evaluate_isolated
from recoveragent.harness import RULES, REFACTOR_REVISION, ServiceCircuitBreaker
from recoveragent.io import read_json, write_json
from recoveragent.student_evaluation import execution_clock
from review_harness_probe import ScriptedTokenizer, generation, block


def probe(root, task, namespace, output, mode):
    started = execution_clock()
    output.mkdir(parents=True, exist_ok=False)
    adapter = AppWorldAdapter(root, task, namespace)
    try:
        initial = adapter.fingerprint()
        tools = [
            t
            for t in adapter.initial_tools()
            if t["function"]["name"]
            in {
                "api_docs__show_api_descriptions",
                "api_docs__show_api_doc",
                "supervisor__show_profile",
                "supervisor__complete_task",
            }
        ]
        if mode == "api_predictor":
            # Script-selected fixed public schema, NOT an actual API Predictor score.
            tools += [
                t
                for t in adapter.catalogue
                if t["function"]["name"] == "simple_note__login"
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
                tool_mode=mode,
                output=output / "interaction",
                checkpoint_id="SCRIPTED_NOT_MODEL",
                protocol_harness=True,
                harness_revision=REFACTOR_REVISION,
                clock=execution_clock,
            )
        event = s.step(generation(block("api_docs__show_api_descriptions", {})))
        assert json.loads(event["feedback"]["content"])["schema_error"] == {
            "path": [],
            "rule": "required",
            "missing_fields": ["app_name"],
        }
        assert adapter.world.num_interactions == 0
        s.step(
            generation(
                block(
                    "api_docs__show_api_doc",
                    {"app_name": "simple_note", "api_name": "login"},
                )
            )
        )
        assert any(t["function"]["name"] == "simple_note__login" for t in s.tools)
        if mode == "api_predictor":
            assert s.tools == tools
        else:
            assert len(s.tools) == len(tools) + 1
        event = s.step(
            generation(
                block(
                    "simple_note__login",
                    {
                        "username": adapter.public_task()["supervisor"]["email"],
                        "password": "SCRIPTED_INVALID_PASSWORD_FOR_PROTOCOL_PROBE",
                    },
                )
            )
        )
        assert event["failure_category"] == "business" and s.harness.rejections == 1
        assert not ServiceCircuitBreaker().observe(event) and not s.stopped
        assert event["appworld_dispatched_count"] == 1
        assert event["tool_results"][0] == s.messages[-1]
        calls_before = adapter.world.num_interactions
        event = s.step(
            generation(
                block("supervisor__show_profile", {})
                + block("supervisor__show_profile", {"unknown": True})
            )
        )
        assert (
            event["failure_category"] == "protocol"
            and adapter.world.num_interactions == calls_before
        )
        assert s.harness.rejections == 2 and not s.stopped
        execute = adapter.execute

        def lose_return(name, args, visible):
            result = execute(name, args, visible)
            if name == "supervisor__complete_task":
                raise OSError("SCRIPTED post-write loss of return")
            return result

        adapter.execute = lose_return
        try:
            s.step(
                generation(
                    block("supervisor__show_profile", {})
                    + block("supervisor__complete_task", {"status": "fail"})
                )
            )
        except OSError:
            pass
        else:
            raise AssertionError("Scripted execution failure was not propagated")
        event = s.events[-1]
        final = adapter.fingerprint()
        interrupted = read_json(output / "interaction/interruption/manifest.json")
        assert final != initial and adapter.world.task_completed()
        assert event["failure_category"] == "worker_environment"
        assert (
            event["appworld_attempted_count"] == 2
            and event["appworld_dispatched_count"] == 1
        )
        assert event["dispatched_count"] == 1 and event.get("inflight_call_id")
        assert (
            interrupted["logical_state_hash"] == final
            and not interrupted["resume_eligible"]
        )
        assert adapter.world.task.ground_truth is None
        assert s.usage["tool_calls"] == 4 and s.harness.rejections == 2
        adapter.save_terminal()
    finally:
        adapter.close()
    evaluated = evaluate_isolated(task, namespace, root, output)
    assert evaluated["status"] == "valid" and evaluated["success"] is False
    result = {
        "mode": mode,
        "runtime_revision": REFACTOR_REVISION,
        "real_tool_calls": s.usage["tool_calls"],
        "real_model_requests": 0,
        "missing_app_name_specific": True,
        "dynamic_or_fixed_schema_correct": True,
        "business_401_kept_without_retry": True,
        "mixed_invalid_batch_zero_calls": True,
        "partial_write_saved": True,
        "known_results": 1,
        "uncertain_calls": 1,
        "incomplete_batch_resume_eligible": False,
        "protocol_rejections": s.harness.rejections,
        "official_category": evaluated["category"],
        "official_success": evaluated["success"],
        "elapsed_seconds": execution_clock() - started,
    }
    write_json(output / "summary.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default="data/appworld")
    p.add_argument("--task", default="e3d6c94_3")
    p.add_argument("--namespace", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--report", required=True)
    a = p.parse_args()
    output = Path(a.output)
    output.mkdir(parents=True, exist_ok=False)
    environment = environment_check()
    rows = [
        probe(a.root, a.task, a.namespace + "_" + mode, output / mode, mode)
        for mode in ("dynamic_discovery", "api_predictor")
    ]
    result = {
        "status": "PASS",
        "scope": "Scripted protocol probes, not model or API Predictor performance",
        "environment": environment,
        "rows": rows,
        "real_model_requests": 0,
        "training_steps": 0,
    }
    write_json(a.report, result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
