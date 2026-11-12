"""Completed-pool Base-SFT handoff tests; no model or GPU execution."""

import copy
import gzip
import json

import pytest

from scripts import train_v2_base_64k as entry
from recoveragent import base_sft
from recoveragent.demonstrations import sha256
from recoveragent.io import digest, read_json, write_json
from recoveragent.v2_data import encode
from recoveragent.v2_tasks import plan
from test_teacher_collection import CharacterTokenizer


@pytest.fixture
def pool(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "validate_demo", lambda x: x)
    monkeypatch.setattr(base_sft, "validate_demo", lambda x: x)
    cfg = read_json("configs/v2_base_sft_64k_20261112.json")
    task = plan()["feedback20"][0]
    history = [
        {"role": "system", "content": "x" * 40000},
        {"role": "user", "content": "y"},
    ]
    sample = {
        "task_id": task,
        "scenario_id": task.rsplit("_", 1)[0],
        "trajectory_id": "synthetic",
        "messages": history + [{"role": "assistant", "content": "ok"}],
        "turns": [{"assistant_index": 2, "tools": [], "history_hash": digest(history)}],
    }
    package = tmp_path / "pool"
    source = package / "samples" / task
    write_json(source / "sample.json", sample)
    rows = encode(sample, CharacterTokenizer(), max_length=65536)
    with gzip.open(source / "examples.jsonl.gz", "wt") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    receipt = {
        "eligible": True,
        "sample_sha256": sha256(source / "sample.json"),
        "examples_sha256": sha256(source / "examples.jsonl.gz"),
        "examples": 1,
        "supervised_tokens": 2,
        "max_sequence_tokens": 40003,
    }
    write_json(source / "encoding.json", receipt)
    (package / "successes.jsonl").write_text(json.dumps(sample) + "\n")
    write_json(
        package / "manifest.json",
        {
            "sources": [{"task_id": task, "sha256": receipt["sample_sha256"]}],
            "files": {"successes.jsonl": sha256(package / "successes.jsonl")},
        },
    )
    cfg["dataset"] = {
        "manifest_sha256": sha256(package / "manifest.json"),
        "tasks": 1,
        "examples": 1,
        "supervised_tokens": 2,
        "max_sequence_tokens": 40003,
    }
    return package, cfg, source


def test_import_retokenizes_exactly_and_preserves_64k_context(pool, tmp_path):
    package, cfg, _ = pool
    result = entry.prepare(package, tmp_path / "encoded", cfg, CharacterTokenizer())
    assert result["remote_retokenization_exact"] and result["examples"] == 1
    assert result["max_sequence_tokens"] == 40003 and result["supervised_tokens"] == 2


@pytest.mark.parametrize(
    "damage", ["manifest", "sample", "encoding", "tokenizer", "counts"]
)
def test_damaged_pool_or_different_tokenization_stops_before_training(
    pool, tmp_path, damage
):
    package, cfg, source = pool
    tokenizer = CharacterTokenizer()
    if damage == "manifest":
        (package / "manifest.json").write_text("{}")
    elif damage == "sample":
        (source / "sample.json").write_text("{}")
    elif damage == "encoding":
        (source / "examples.jsonl.gz").write_bytes(b"wrong")
    elif damage == "counts":
        cfg["dataset"]["supervised_tokens"] += 1
    else:
        tokenizer = copy.copy(tokenizer)
        tokenizer.apply_chat_template = lambda messages, **kw: "changed"
    with pytest.raises(ValueError):
        entry.prepare(package, tmp_path / "rejected", cfg, tokenizer)


def test_formal_acceptance_requires_actual_steps_parameter_change_and_reload():
    cfg = read_json("configs/v2_base_sft_64k_20261112.json")
    data = {"examples": 5261, "examples_sha256": "data"}
    record = {
        "profile": "formal",
        "stage": "S0_base",
        "parent": None,
        "reload_passed": True,
        "adapter_sha256_before": "before",
        "adapter_sha256_after": "after",
        "data_sha256": "data",
        "optimizer_steps": 658,
        "sequence_limit": 65536,
    }
    entry.validate_stage(record, "formal", data, cfg)
    for key, value in [
        ("optimizer_steps", 657),
        ("reload_passed", False),
        ("adapter_sha256_after", "before"),
        ("parent", "old_S0"),
    ]:
        with pytest.raises(ValueError):
            entry.validate_stage({**record, key: value}, "formal", data, cfg)
