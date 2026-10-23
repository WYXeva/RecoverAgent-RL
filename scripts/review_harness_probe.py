"""Scripted real AppWorld interruption probe; no model/Teacher performance claims."""

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from recoveragent import student
from recoveragent.adapter import AppWorldAdapter
from recoveragent.evaluation import environment_check, evaluate_isolated
from recoveragent.harness import RULES
from recoveragent.io import read_json, write_json


class ScriptedTokenizer:
    """Character codec ONLY for injecting scripted generations into the runner."""

    chat_template = "SCRIPTED_PROBE_NOT_QWEN_TOKENIZATION"
    eos_token_id = 0

    def apply_chat_template(self, messages, **kwargs):
        return json.dumps([messages, kwargs["tools"]])

    def __call__(self, text, **kwargs):
        return {"input_ids": list(text.encode())}

    def decode(self, ids, **kwargs):
        return "".join("<|im_end|>" if i == 0 else chr(i) for i in ids)


def generation(text):
    return lambda request: {
        "policy": request["policy"],
        "input_token_ids": request["input_token_ids"],
        "token_ids": [ord(c) for c in text] + [0],
        "finish_reason": "stop",
    }


def block(name, args):
    return (
        "<tool_call>" + json.dumps({"name": name, "arguments": args}) + "</tool_call>"
    )


def probe(root, task, namespace, output, report):
    environment = environment_check()
    adapter = AppWorldAdapter(root, task, namespace)
    try:
        initial = adapter.fingerprint()
        # Small public tool subset avoids measuring a fake codec's context length.
        tools = [
            t
            for t in adapter.initial_tools()
            if t["function"]["name"]
            in {"supervisor__show_profile", "supervisor__complete_task"}
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
                output=output,
                protocol_harness=True,
            )
        s.step(generation("Stopped unexpectedly."))
        assert s.events[-1]["protocol_rejection"] == "unexpected_stop"
        s.step(
            generation(
                block("supervisor__show_profile", {})
                + block("supervisor__show_profile", {"illegal": 1})
            )
        )
        assert s.events[-1]["protocol_rejection"] == "invalid_arguments"
        assert adapter.world.num_interactions == 0 and adapter.fingerprint() == initial
        execute = adapter.execute

        def lose_result(name, arguments, visible):
            result = execute(name, arguments, visible)
            if name == "supervisor__complete_task":
                # Inject AFTER a real write; no official code or validator changes.
                raise OSError("scripted loss of returned write result")
            return result

        adapter.execute = lose_result
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
            raise AssertionError("Expected scripted interruption")
        after = adapter.fingerprint()
        assert after != initial and adapter.world.task_completed()
        event = s.events[-1]
        assert event["dispatched_count"] == 1 and event["appworld_attempted_count"] == 2
        assert event["interruption"]["status"] == "saved"
        manifest = read_json(output / "interruption/manifest.json")
        assert (
            manifest["logical_state_hash"] == after and not manifest["resume_eligible"]
        )
        assert len(read_json(output / "interruption/dbs/model_hashes.json")) > 0
        adapter.import_state(output / "interruption/dbs", "review_roundtrip")
        assert adapter.fingerprint() == after
        adapter.save_terminal()
    finally:
        adapter.close()
    evaluated = evaluate_isolated(task, namespace, root, output)
    # Some official tests assume the requested object already exists. An
    # incomplete task can therefore raise a non-assertion exception; preserve
    # that distinction instead of demanding a fabricated strict zero reward.
    assert evaluated["status"] in {"valid", "evaluation_error"}
    assert evaluated["success"] is not True
    write_json(
        report,
        {
            "scope": "scripted generations and injected post-write return loss on real AppWorld; NOT Teacher/Student performance",
            "environment": environment,
            "unexpected_stop_zero_calls": True,
            "invalid_mixed_batch_zero_calls": True,
            "real_tool_calls": 2,
            "returned_results": 1,
            "uncertain_call_preserved": True,
            "actual_state_changed": True,
            "saved_state_matches": True,
            "state_roundtrip_matches": True,
            "incomplete_history_not_resumable": True,
            "official_evaluation_status": evaluated["status"],
            "official_success": evaluated["success"],
            "evaluation_exception_types": evaluated["exception_types"],
            "source_directory": str(output),
            "model_requests": 0,
            "new_teacher_demonstrations": 0,
            "training_steps": 0,
        },
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default="data/appworld")
    p.add_argument("--task", default="c901732_2")
    p.add_argument("--namespace", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", required=True)
    a = p.parse_args()
    probe(a.root, a.task, a.namespace, a.output, a.report)
    print(json.dumps({"report": a.report, "real_tool_calls": 2, "model_requests": 0}))


if __name__ == "__main__":
    main()
