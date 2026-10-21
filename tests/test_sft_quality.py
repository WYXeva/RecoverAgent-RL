"""Software-only negative checks; actual tokenizer audits are recorded separately."""

import copy
import json

import pytest

from recoveragent import sft_quality
from recoveragent.io import digest


class Tokenizer:
    pad_token_id = 0

    def decode(self, ids, **kwargs):
        return "".join(chr(i) for i in ids)


def fixture():
    rows, turns, messages = [], [], []
    for n in [2, 8]:
        ids = [120] * n + [ord(c) for c in "ok<|im_end|>\n"]
        rows.append(
            {
                "input_ids": ids,
                "target_start": n,
                "labels": [-100] * n + ids[n:],
                "loss_mask": [0] * n + [1] * (len(ids) - n),
                "attention_mask": [1] * len(ids),
                "tools_hash": digest([]),
            }
        )
        turns.append({"assistant_index": len(messages), "tools": []})
        messages.append({"role": "assistant", "content": "ok"})
    return rows, {
        "trajectory_id": "synthetic",
        "task_id": "synthetic_1",
        "scenario_id": "synthetic",
        "turns": turns,
        "messages": messages,
    }


def test_overlength_defers_whole_trajectory_without_clipping(tmp_path, monkeypatch):
    rows, sample = fixture()
    monkeypatch.setattr(
        sft_quality, "assistant_examples", lambda *a, **k: copy.deepcopy(rows)
    )
    r = sft_quality.audit_samples(
        [sample], Tokenizer(), tmp_path / "short", len(rows[0]["input_ids"])
    )
    assert r["all_clean_token_counts"]["examples"] == 2
    assert r["all_clean_token_counts"]["overlength_turns"] == 1
    assert r["ready_complete_demonstrations"] == 0
    assert (tmp_path / "short/ready_assistant_examples.jsonl").read_text() == ""
    r = sft_quality.audit_samples([sample], Tokenizer(), tmp_path / "long", 100)
    assert r["ready_complete_demonstrations"] == 1
    saved = [
        json.loads(s)
        for s in (tmp_path / "long/ready_assistant_examples.jsonl")
        .read_text()
        .splitlines()
    ]
    assert saved == rows


@pytest.mark.parametrize("damage", ["mask", "schema", "missing_turn"])
def test_corrupt_mask_schema_or_target_count_cannot_pass(tmp_path, monkeypatch, damage):
    rows, sample = fixture()
    if damage == "mask":
        rows[0]["labels"][0] = rows[0]["input_ids"][0]
    elif damage == "schema":
        rows[0]["tools_hash"] = "future_schema"
    else:
        rows.pop()
    monkeypatch.setattr(sft_quality, "assistant_examples", lambda *a, **k: rows)
    with pytest.raises(ValueError):
        sft_quality.audit_samples([sample], Tokenizer(), tmp_path / "bad", 100)


def test_qwen_target_budget_is_distinct_from_teacher_token_count(tmp_path, monkeypatch):
    rows, sample = fixture()
    ids = [120] * 2 + [ord(c) for c in "ok" + " " * 4100 + "<|im_end|>\n"]
    rows[0].update(
        input_ids=ids,
        labels=[-100] * 2 + ids[2:],
        loss_mask=[0] * 2 + [1] * (len(ids) - 2),
        attention_mask=[1] * len(ids),
    )
    monkeypatch.setattr(sft_quality, "assistant_examples", lambda *a, **k: rows)
    r = sft_quality.audit_samples(
        [sample], Tokenizer(), tmp_path / "long_target", 10000
    )
    assert r["all_clean_token_counts"]["overlength_turns"] == 0
    assert r["all_clean_token_counts"]["over_output_limit_turns"] == 1
    assert r["ready_complete_demonstrations"] == 0
