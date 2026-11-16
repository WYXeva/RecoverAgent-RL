"""Exact receipt compression and retirement limited to new intermediate checkpoints."""

import pytest

from recoveragent.io import read_json, write_json
from recoveragent.s2_storage import (
    archive_requests,
    request_record,
    retain_committed,
    paired_inputs,
)


def test_receipts_roundtrip_without_changing_values(tmp_path):
    value = {"input_token_ids": [1, 2, 3], "logprobs": [-0.1, -0.2], "text": "original"}
    write_json(tmp_path / "session/requests/001.request.json", value)
    archive_requests(tmp_path)
    assert not (tmp_path / "session/requests/001.request.json").exists()
    assert request_record(tmp_path, "001.request.json") == value
    archive_requests(tmp_path)
    assert read_json(tmp_path / "session/requests_archive.json")[
        "exact_raw_records_preserved"
    ]
    (tmp_path / "session/requests.tar.gz").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        archive_requests(tmp_path)


def test_only_retire_superseded_intermediates_keep_round_and_latest(tmp_path):
    updates = []
    for i in range(3):
        directory = tmp_path / "updates" / str(i)
        actor = directory / "actor"
        actor.mkdir(parents=True)
        (actor / "adapter_model.safetensors").write_bytes(b"synthetic")
        write_json(
            actor / "checkpoint_receipt.json",
            {"files": {"adapter_model.safetensors": "hash"}},
        )
        (directory / "optimizer.pt").write_bytes(b"synthetic optimizer")
        write_json(
            directory / "committed.json",
            {
                "updated": True,
                "actor": str(actor),
                "actor_sha256": "hash",
                "optimizer": str(directory / "optimizer.pt"),
            },
        )
        updates.append(str(directory / "committed.json"))
    state = {
        "actor": str(tmp_path / "updates/2/actor"),
        "optimizer": str(tmp_path / "updates/2/optimizer.pt"),
        "round_evaluations": {
            "0": {
                "actor": str(tmp_path / "updates/0/actor"),
                "optimizer": str(tmp_path / "updates/0/optimizer.pt"),
            }
        },
        "updates": updates,
    }
    retain_committed(state, tmp_path)
    assert (tmp_path / "updates/0/actor/adapter_model.safetensors").exists()
    assert (tmp_path / "updates/2/optimizer.pt").exists()
    assert not (tmp_path / "updates/1/actor/adapter_model.safetensors").exists()
    assert not (tmp_path / "updates/1/optimizer.pt").exists()
    assert (tmp_path / "updates/1/actor/checkpoint_receipt.json").exists()
    assert read_json(tmp_path / "updates/1/retention.json")["loadable"] is False


def test_paired_actual_inputs_checked_even_after_archival(tmp_path):
    q = {
        "messages": [],
        "tools": [],
        "input_token_ids": [1],
        "max_input_tokens": 65536,
        "max_new_tokens": 4,
        "temperature": 0.8,
        "top_p": 1,
        "seed": 42,
    }
    rows = []
    for name in ("S0", "round1"):
        directory = tmp_path / name
        write_json(directory / "session/requests/001.request.json", q)
        archive_requests(directory)
        rows.append(
            {
                "directory": str(directory),
                "task_id": "t",
                "success": True,
                "initial_state_hash": "same",
            }
        )
    assert paired_inputs(rows[:1], rows[1:])[0]["same_input"]
    rows[1]["initial_state_hash"] = "changed"
    with pytest.raises(ValueError, match="differs"):
        paired_inputs(rows[:1], rows[1:])
