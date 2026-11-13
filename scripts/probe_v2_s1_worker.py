"""Real CPU environment/snapshot probe with a synthetic reply, no model calls."""

import argparse
import json
from pathlib import Path

from freezegun import configure

from recoveragent.adapter import AppWorldAdapter, call_code
from recoveragent.execution_v6 import SYSTEM_PROMPT
from recoveragent.io import write_json
from recoveragent.p3c import save_boundary
from recoveragent.recovery_suffix import restore_teacher_start
from recoveragent.runner import now
from recoveragent.schema_compatibility import SCHEMA_REVISION
from recoveragent.student import StudentSession
from recoveragent.v2_model import load_tokenizer
from recoveragent.v2_runtime import S1_RUNTIME
from recoveragent.v2_s1_batch import configuration
from recoveragent.v2_tasks import context_for


def probe(root, output):
    cfg = configuration()
    identity = cfg["models"]["primary"]
    tokenizer = load_tokenizer(identity)
    configure(extend_ignore_list=["transformers", "torch", "huggingface_hub"])
    task = "d8e490b_1"
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    adapter = AppWorldAdapter(
        root, task, output.name + "_source", research=context_for(task, training=True)
    )
    try:
        adapter.configure_schema_compatibility(SCHEMA_REVISION, output / "schema")
        session = StudentSession(
            adapter,
            tokenizer,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(adapter.public_task())},
            ],
            adapter.initial_tools(),
            tool_mode="dynamic_discovery",
            protocol_harness=True,
            answer_guard=True,
            clock=now,
            execution_protocol=S1_RUNTIME,
            model_contract=identity,
            checkpoint_id="synthetic-no-model-probe",
            output=output / "session",
            **cfg["student_limits"],
        )
        session.runtime_revision = S1_RUNTIME
        text = (
            "<tool_call><function=api_docs__show_api_doc><parameter=app_name>gmail</parameter>"
            "<parameter=api_name>show_category_sizes</parameter></function></tool_call><|im_end|>"
        )
        ids = tokenizer(text, add_special_tokens=False)["input_ids"]

        def synthetic(request):
            return {
                "policy": request["policy"],
                "input_token_ids": request["input_token_ids"],
                "token_ids": ids,
                "text": text,
                "finish_reason": "eos",
                "logprobs": [0.0] * len(ids),
                "synthetic": True,
                "generation_seconds": 0.0,
            }

        session.step(synthetic)
        ref = save_boundary(
            session, output / "snapshot", "post_action", {"synthetic": True}
        )
        before = adapter.fingerprint()
    finally:
        adapter.close()
    restored = AppWorldAdapter(
        root, task, output.name + "_restored", research=context_for(task, training=True)
    )
    try:
        messages, tools, _ = restore_teacher_start(
            restored, ref["snapshot_ref"], output / "restore", {}
        )
        assert (
            before == restored.fingerprint()
            and messages[:-1] == session.messages
            and tools == session.tools
        )
        call_code(
            "gmail__show_category_sizes",
            {"read": None, "access_token": "synthetic-only-no-execution"},
            tools,
        )
    finally:
        restored.close()
    report = {
        "passed": True,
        "runtime": S1_RUNTIME,
        "model_requests": 0,
        "real_task_scores": 0,
        "synthetic_replies": 1,
        "public_documentation_calls": 1,
        "snapshot_restore": True,
        "tokenizer_loaded_during_world_cleanup": True,
        "tools_and_history_equal": True,
    }
    write_json(output / "summary.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    probe(args.root, args.output)
