"""Qualified partial collections, S0 lineage and fixed dev24; no model calls."""

import copy
import gzip
import json

import pytest

from recoveragent import v2_s1_retrain as retrain
from recoveragent.demonstrations import sha256
from recoveragent.io import read_json, write_json
from recoveragent.v2_data import read_rows
from recoveragent.v2_tasks import plan


def sources(tmp_path):
    tasks = [r["task_id"] for r in plan()["rows"] if r["role"] == "train"][:3]
    manifest = {"student": {"id": "synthetic"}, "groups": {}}
    cfg = {
        "models": {"primary": manifest["student"]},
        "selected_model": "primary",
        "data_groups": {},
        "recovery_sft": {"recovery_supervised_token_fraction": 0.5},
        "student_limits": {"max_input_tokens": 65536},
        "s0": {"identity": "accepted_parent"},
    }
    kinds = ("recovery_suffix", "fresh_full", "original_replay")
    originals = []
    for index, (kind, task) in enumerate(zip(kinds, tasks, strict=True)):
        row = {
            "task_id": task,
            "input_ids": [10, 11, 12, 13],
            "labels": [-100, -100, 12, 13],
            "loss_mask": [0, 0, 1, 1],
            "target_start": 2,
            "assistant_index": index + 3,
            "example_weight": 1.0,
        }
        originals.append(copy.deepcopy(row))
        p = tmp_path / (kind + ".jsonl.gz")
        with gzip.open(p, "wt") as stream:
            stream.write(json.dumps(row) + "\n")
        manifest["groups"][kind] = {"file": p.name, "sha256": sha256(p)}
        cfg["data_groups"][kind] = {
            "task_ids": [task],
            "examples": 1,
            "supervised_tokens": 2,
        }
    write_json(tmp_path / "manifest.json", manifest)
    cfg["data_manifest_sha256"] = sha256(tmp_path / "manifest.json")
    return manifest, cfg, originals


def test_stopped_qualified_sources_mix_all_targets_without_replaying_collection(
    tmp_path, monkeypatch
):
    _, cfg, originals = sources(tmp_path)
    monkeypatch.setattr(retrain, "verify_lock", lambda *_: {"lock_sha256": "test"})
    output = tmp_path / "new_training"
    retrain.prepare(output, {"data_sources": str(tmp_path)}, cfg)
    mixed = read_rows(output / "training_data/examples.jsonl")
    assert len(mixed) == 3
    for before, after in zip(originals, mixed, strict=True):
        for field in ("task_id", "input_ids", "labels", "loss_mask", "assistant_index"):
            assert after[field] == before[field]
    receipt = read_json(output / "training_data/manifest.json")
    assert receipt["groups"]["recovery_suffix"]["objective_fraction"] == 0.5
    assert receipt["groups"]["fresh_full"]["objective_fraction"] == 0.25
    assert receipt["groups"]["original_replay"]["objective_fraction"] == 0.25
    assert not receipt["truncated"] and not receipt["validation_data_used"]
    with pytest.raises(FileExistsError):
        retrain.prepare(output, {"data_sources": str(tmp_path)}, cfg)


@pytest.mark.parametrize("change", ["file", "count", "overlap", "dev"])
def test_changed_or_leaking_sources_rejected(tmp_path, change):
    manifest, cfg, _ = sources(tmp_path)
    if change == "file":
        (tmp_path / manifest["groups"]["fresh_full"]["file"]).write_bytes(b"changed")
    elif change == "count":
        cfg["data_groups"]["fresh_full"]["examples"] += 1
    else:
        kind = "fresh_full"
        target = tmp_path / manifest["groups"][kind]["file"]
        with gzip.open(target, "rt") as f:
            row = json.loads(f.readline())
        task = (
            cfg["data_groups"]["recovery_suffix"]["task_ids"][0]
            if change == "overlap"
            else plan()["dev24"][0]
        )
        row["task_id"] = task
        with gzip.open(target, "wt") as f:
            f.write(json.dumps(row) + "\n")
        manifest["groups"][kind]["sha256"] = sha256(target)
        cfg["data_groups"][kind]["task_ids"] = [task]
    with pytest.raises(ValueError, match="changed|scope"):
        retrain.load_groups(tmp_path, manifest, cfg)


def test_new_runtime_preserves_evaluation_and_old_locks_still_reject():
    from recoveragent import teacher_followup
    from recoveragent.student import verify_student_runtime
    from recoveragent.v2_runtime import verify_lock, S1_RUNTIME

    cfg = retrain.configuration()
    assert cfg["dev_task_ids"] == plan()["dev24"]
    assert verify_student_runtime(retrain.RUNTIME)["revision"] == retrain.RUNTIME
    assert len(cfg["data_groups"]["fresh_full"]["task_ids"]) == 21
    for old in (S1_RUNTIME,):
        with pytest.raises(ValueError, match="source|Incomplete"):
            verify_lock(old)
    with pytest.raises(ValueError, match="source|Incomplete"):
        teacher_followup.verify_lock(teacher_followup.RETRY)
