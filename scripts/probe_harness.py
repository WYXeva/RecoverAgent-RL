"""Real AppWorld dispatch and pinned-tokenizer probes, with scripted generations.

This is deliberately not a Teacher demonstration or a Student model score.
"""

import argparse
import copy
import json
from pathlib import Path

from recoveragent.io import write_json


def world_probe(output, root, task, namespace):
    from recoveragent.adapter import AppWorldAdapter
    from recoveragent.harness import check_batch, ProtocolHarness, ProtocolViolation
    from recoveragent.tool_modes import execute_with_mode

    rows = []
    # Independent serial worlds, both start at the original state.
    for mode in ("dynamic_discovery", "api_predictor"):
        adapter = AppWorldAdapter(root, task, namespace + "_" + mode)
        try:
            tools = adapter.initial_tools()
            initial = adapter.fingerprint()
            discover = {
                "id": "discovery",
                "type": "function",
                "function": {
                    "name": "api_docs__show_api_doc",
                    "arguments": '{"app_name":"spotify","api_name":"login"}',
                },
            }
            invisible = {
                "id": "unopened",
                "type": "function",
                "function": {"name": "spotify__login", "arguments": "{}"},
            }
            batch = {
                "role": "assistant",
                "content": "",
                "tool_calls": [discover, invisible],
            }
            try:
                check_batch(batch, [], tools)
            except ProtocolViolation as exc:
                feedback = ProtocolHarness(mode).reject(exc.code)
            else:
                raise AssertionError(
                    "Same-batch discovery incorrectly admitted an invisible tool"
                )
            assert (
                adapter.fingerprint() == initial and adapter.world.num_interactions == 0
            )
            valid = dict(batch, tool_calls=[discover])
            check_batch(valid, [feedback], tools)
            _, after = execute_with_mode(
                adapter,
                discover["function"]["name"],
                discover["function"]["arguments"],
                tools,
                mode,
            )
            opened = "spotify__login" in {t["function"]["name"] for t in after}
            assert opened == (mode == "dynamic_discovery")
            assert adapter.world.num_interactions == 1
            rows.append(
                {
                    "mode": mode,
                    "rejected_batch_real_calls": 0,
                    "state_unchanged_on_rejection": True,
                    "subsequent_real_discovery_calls": 1,
                    "login_visible_next_turn": opened,
                }
            )
        finally:
            adapter.close()
    result = {
        "scope": "scripted protocol probe, not Teacher or Student result",
        "real_tool_calls": 2,
        "model_requests": 0,
        "rows": rows,
    }
    write_json(output, result)


def mask_probe(output, synthetic_run):
    from recoveragent.base_sft import load_tokenizer, assistant_examples, pad_examples
    from recoveragent.demonstrations import as_demo, validate_receipts
    from recoveragent.io import read_json
    from recoveragent.student import parse_batch

    directory = Path(synthetic_run)
    trajectory = read_json(directory / "trajectory.json")
    validate_receipts(trajectory, directory)
    record = as_demo(trajectory)
    tokenizer = load_tokenizer()
    examples = assistant_examples(record, tokenizer)
    expected = [t for t in record["turns"] if t.get("supervise") is not False]
    assert len(examples) == len(expected) == 1
    for example, turn in zip(examples, expected):
        start, ids = example["target_start"], example["input_ids"]
        assert example["labels"][:start] == [-100] * start
        assert example["labels"][start:] == ids[start:]
        parsed = parse_batch(tokenizer.decode(ids[start:]), "probe", "stop")
        actual = [
            {
                "name": c["function"]["name"],
                "arguments": json.loads(c["function"]["arguments"]),
            }
            for c in parsed.get("tool_calls", [])
        ]
        target = record["messages"][turn["assistant_index"]]
        want = [
            {
                "name": c["function"]["name"],
                "arguments": json.loads(c["function"]["arguments"]),
            }
            for c in target.get("tool_calls", [])
        ]
        assert actual == want
        assert all(
            m["role"] != "assistant"
            for m in record["messages"][: turn["assistant_index"]]
        )
    shorter = copy.deepcopy(examples[0])
    for key in ("input_ids", "labels", "attention_mask", "loss_mask"):
        shorter[key] = shorter[key][:-1]
    padded = pad_examples([examples[0], shorter], tokenizer.pad_token_id)
    assert padded[1]["labels"][-1] == -100 and padded[1]["attention_mask"][-1] == 0
    write_json(
        output,
        {
            "scope": "real tokenizer on synthetic rejection/correction; no model weights",
            "samples": len(examples),
            "rejected_turns": trajectory["harness"]["rejections"],
            "supervised_tokens": sum(
                sum(v != -100 for v in x["labels"]) for x in examples
            ),
            "max_sequence": max(len(x["input_ids"]) for x in examples),
            "assistant_only": True,
            "padding_mask": True,
            "student_parser_roundtrip": True,
            "model_requests": 0,
            "training_steps": 0,
        },
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["world", "mask"])
    p.add_argument("--output", required=True)
    p.add_argument("--root", default="data/appworld")
    p.add_argument("--task", default="c901732_2")
    p.add_argument("--namespace", default="harness_protocol_probe")
    p.add_argument("--synthetic-run")
    a = p.parse_args()
    if a.mode == "world":
        world_probe(a.output, a.root, a.task, a.namespace)
    else:
        mask_probe(a.output, a.synthetic_run)
    print(json.dumps({"output": a.output, "model_requests": 0, "training_steps": 0}))


if __name__ == "__main__":
    main()
