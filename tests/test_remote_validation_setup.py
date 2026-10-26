"""Software gates only; this does not simulate successful GPU training."""

import copy
import importlib.util
import json

import pytest

from recoveragent.demonstrations import sha256
from recoveragent.io import read_json, write_json
from recoveragent.p3c import verify_source
from recoveragent.student_evaluation import verify_execution_runtime


def setup_module_fixture(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "prepare_student_validation", "scripts/prepare_student_validation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    training, dataset = tmp_path / "training", tmp_path / "dataset"
    write_json(dataset / "manifest.json", {"synthetic": True})
    stages = ["short_smoke", "length_smoke", "formal"]
    summary = {
        "status": "PASS",
        "manifest_sha256": sha256(dataset / "manifest.json"),
        "stages": [
            {"stage": s, "status": "PASS", "optimizer_steps": 1} for s in stages
        ],
    }
    write_json(training / "summary.json", summary)
    meta = {
        "status": "PASS",
        "model": module.MODEL,
        "revision": module.REVISION,
        "dataset_manifest_sha256": summary["manifest_sha256"],
        "parameter_update_and_reload_pass": True,
        "checkpoint_reloaded": True,
        "trainable_before_hash": "synthetic_before",
        "trainable_after_hash": "synthetic_after",
        "restored_trainable_hash": "synthetic_after",
    }
    for stage in stages:
        write_json(training / stage / "recoveragent_checkpoint.json", meta)
    for name in (
        "adapter_config.json",
        "adapter_model.safetensors",
        "run_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
    ):
        (training / "formal" / name).write_text("synthetic")
    monkeypatch.setattr(
        module, "TEMPLATE_SHA256", sha256(training / "formal/chat_template.jinja")
    )
    monkeypatch.setattr(module, "verify_checkpoint", lambda *args: {})
    return module, training, dataset, summary, meta


def test_new_source_locks_and_original_locks_coexist():
    assert len(verify_source()) == 64
    assert verify_execution_runtime()["revision"] == "p3b_shared_execution_20261026"
    assert read_json("configs/p3c_runtime_lock.json")["runtime_revision"] == (
        "p3c_complete_boundaries_20261025"
    )


@pytest.mark.parametrize(
    "failure", ["missing_smoke", "unchanged_weights", "wrong_data"]
)
def test_new_collection_requires_all_training_gates(tmp_path, monkeypatch, failure):
    module, training, dataset, summary, meta = setup_module_fixture(
        tmp_path, monkeypatch
    )
    if failure == "missing_smoke":
        summary["stages"].pop(0)
        (training / "summary.json").write_text(json.dumps(summary))
    elif failure == "unchanged_weights":
        bad = copy.deepcopy(meta)
        bad["trainable_before_hash"] = bad["trainable_after_hash"]
        (training / "length_smoke/recoveragent_checkpoint.json").write_text(
            json.dumps(bad)
        )
    else:
        (dataset / "manifest.json").write_text('{"changed":true}')
    output = tmp_path / "prepared"
    with pytest.raises(ValueError):
        module.prepare(training, dataset, tmp_path / "world", output)
    assert not output.exists()


def test_new_configs_bind_fresh_checkpoint_and_keep_fixed_task_lists(
    tmp_path, monkeypatch
):
    module, training, dataset, _, _ = setup_module_fixture(tmp_path, monkeypatch)
    output = tmp_path / "prepared"
    module.prepare(training, dataset, tmp_path / "world", output)
    config = read_json(output / "p3c_config.json")
    assert config["harness_runtime_revision"] == "shared_execution_20261025"
    assert config["checkpoint"] == str((training / "formal").resolve())
    assert config["checkpoint_release_sha256"] == sha256(
        output / "checkpoint_release.json"
    )
    assert config["minimum_gpu_memory_mib"] == 75000
    old = read_json("configs/p3c_collection_20261025.json")
    assert config["student_limits"] == old["student_limits"]
    assert config["task_manifest_hash"] == old["task_manifest_hash"]
    assert (
        read_json(output / "paired_config.json")["cases"]
        == read_json("configs/p3b_paired_evaluation_20261023.json")["cases"]
    )
    with pytest.raises(FileExistsError):
        module.prepare(training, dataset, tmp_path / "world", output)
