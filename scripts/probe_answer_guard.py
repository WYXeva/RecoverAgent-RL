"""Format-guard infrastructure evidence. Scripted calls are NEVER Teacher data."""

import argparse
import copy
import json
from pathlib import Path

from recoveragent.answer_guard import AnswerGuard, COMPLETE, numeric_representation
from recoveragent.io import digest, read_json, write_json


def offline(run, scenario):
    rows = []
    for path in sorted(Path(run).glob("teacher_*/trajectory.json")):
        t = read_json(path)
        if t["scenario_id"] != scenario:
            continue
        instruction = json.loads(t["messages"][1]["content"])["instruction"]
        calls = [
            c
            for m in t["messages"]
            for c in m.get("tool_calls", [])
            if c["function"]["name"] == COMPLETE
        ]
        if len(calls) != 1:
            raise ValueError("Expected one historical completion")
        original = copy.deepcopy(calls)
        guard = AnswerGuard(instruction)
        verdict = guard.inspect(calls)
        assert verdict[0]["error"] == "harness_format_error" and calls == original
        value = numeric_representation(
            json.loads(calls[0]["function"]["arguments"])["answer"]
        )
        assert value == int(value)  # These four historical cases are whole numbers.
        synthetic = copy.deepcopy(calls)
        synthetic[0]["function"]["arguments"] = json.dumps({"answer": int(value)})
        assert guard.inspect(synthetic) == [None] and guard.correction_valid
        rows.append(
            {
                "trajectory_id": t["trajectory_id"],
                "task_id": t["task_id"],
                "tool_mode": t["tool_mode"],
                "source_sha256": __import__("hashlib")
                .sha256(path.read_bytes())
                .hexdigest(),
                "raw_call_hash": digest(calls),
                "raw_call_unchanged": calls == original,
                "initial_rejected": True,
                "synthetic_same_value_correction_accepted": True,
            }
        )
    if len(rows) != 4:
        raise ValueError("Expected all four historical natural format failures")
    return {
        "status": "PASS",
        "scope": "offline public-history replay; synthetic correction, no new Teacher demonstration",
        "rows": rows,
        "model_requests": 0,
        "real_tool_calls": 0,
        "official_evaluation": "NOT_RUN",
        "hidden_answers_loaded": False,
        "old_rewards_unchanged": True,
    }


def world_probe(root, task, namespace):
    from recoveragent.adapter import AppWorldAdapter

    adapter = AppWorldAdapter(root, task, namespace)
    try:
        guard = AnswerGuard(adapter.public_task()["instruction"])
        assert guard.contract["kind"] == "money"
        call = {
            "id": "scripted",
            "type": "function",
            "function": {"name": COMPLETE, "arguments": '{"answer":"$12.5"}'},
        }
        before = adapter.fingerprint()
        assert guard.inspect([call])[0]["appworld_executed"] is False
        assert before == adapter.fingerprint() and not adapter.world.task_completed()
        # Deliberately arbitrary diagnostic value, not a task answer or model output.
        call["function"]["arguments"] = '{"answer":12.5}'
        assert guard.inspect([call]) == [None]
        result, _ = adapter.execute(
            COMPLETE, call["function"]["arguments"], adapter.initial_tools()
        )
        assert "Execution failed" not in result and adapter.world.task_completed()
        after = adapter.fingerprint()
        assert before != after
        return {
            "status": "PASS",
            "scope": "real AppWorld state; scripted calls, not autonomous Teacher performance",
            "task_id": task,
            "blocked_call_state_unchanged": True,
            "blocked_call_did_not_complete": True,
            "corrected_call_executed_unchanged": True,
            "real_tool_calls": 1,
            "synthetic_model_replies": 2,
            "model_requests": 0,
            "official_evaluation": "NOT_RUN",
            "teacher_demonstrations_generated": 0,
            "hidden_answers_loaded": False,
            "before_hash": before,
            "after_hash": after,
        }
    finally:
        adapter.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["offline", "world", "tokenizer"])
    p.add_argument("--sample")
    p.add_argument("--run")
    p.add_argument("--scenario", default="76f2c72")
    p.add_argument("--root", default="data/appworld")
    p.add_argument("--task", default="76f2c72_1")
    p.add_argument("--namespace", default="answer_guard_probe_20261021_a")
    p.add_argument("--report", required=True)
    args = p.parse_args()
    if args.mode == "offline":
        result = offline(args.run, args.scenario)
    elif args.mode == "world":
        result = world_probe(args.root, args.task, args.namespace)
    else:
        from recoveragent.base_sft import (
            assistant_examples,
            load_tokenizer,
            MODEL,
            REVISION,
            TEMPLATE_SHA256,
        )

        sample = json.loads(Path(args.sample).read_text())
        if sample["trajectory_id"] != "teacher_synthetic":
            raise ValueError("This probe expects the explicit software fixture")
        tokenizer = load_tokenizer()
        rows = assistant_examples(sample, tokenizer)
        assert len(rows) == 1
        row = rows[0]
        prefix = tokenizer.decode(row["input_ids"][: row["target_start"]])
        target = tokenizer.decode(row["input_ids"][row["target_start"] :])
        assert "$368" in prefix and "harness_format_error" in prefix
        assert "$368" not in target and "368" in target
        assert set(row["labels"][: row["target_start"]]) == {-100}
        assert row["assistant_index"] == sample["turns"][1]["assistant_index"]
        result = dict(
            status="PASS",
            scope="real pinned Qwen tokenizer; synthetic Teacher/world/evaluation fixture, not training data",
            model=MODEL,
            revision=REVISION,
            template_sha256=TEMPLATE_SHA256,
            fixture_hash=digest(sample),
            input_tokens=len(row["input_ids"]),
            supervised_tokens=sum(row["loss_mask"]),
            context_only_rejected_turns=1,
            supervised_corrected_turns=1,
            model_requests=0,
            training_started=False,
            weights_loaded=False,
        )
    write_json(args.report, result)
    print({k: v for k, v in result.items() if k != "rows"})


if __name__ == "__main__":
    main()
