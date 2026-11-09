"""Synthetic checkpoint handoff tests; no pretrained model or real task execution."""

import copy
import json

import pytest

from recoveragent.demonstrations import sha256
from recoveragent.io import files_manifest, read_json, write_json
from recoveragent.v2_resume import establish_base, verified_base
from recoveragent.v2_round import config
from recoveragent.v2_tasks import plan


@pytest.fixture
def completed_base(tmp_path):
    source, data = tmp_path / "prior", tmp_path / "data"
    data.mkdir()
    (data / "examples.jsonl").write_text("{}\n" * 9)
    old = read_json("configs/v2_round1_cpu_offload_20261109.json")
    cfg = config()
    identity = cfg["models"][cfg["selected_model"]]
    data_hash = sha256(data / "examples.jsonl")
    write_json(
        data / "preparation.json",
        {
            "model": identity,
            "examples": 9,
            "examples_sha256": data_hash,
        },
    )
    write_json(source / "run_config.json", old)
    write_json(
        source / "preflight.json",
        {
            "source": {"lock_sha256": sha256(old["source_lock"])},
            "deployment": {"job_deadline_epoch": 100000},
        },
    )
    write_json(
        source / "round_summary.json",
        {
            "status": "stopped",
            "feedback": [],
            "recovery": [],
            "generated": [],
            "evaluations": {},
            "training_generated_tokens": 0,
        },
    )
    frozen = plan()
    write_json(
        source / "task_lists.json",
        {
            "feedback20": frozen["feedback20"],
            "dev24": frozen["dev24"],
            "split_manifest_sha256": frozen["manifest_sha256"],
        },
    )
    for stage, profile, steps, examples in [
        ("smoke_short", "short", 2, 4),
        ("smoke_long", "long", 1, 1),
        ("S0_base", "formal", 2, 9),
    ]:
        path = source / stage
        path.mkdir()
        (path / "synthetic_adapter.txt").write_text("synthetic updated parameters")
        write_json(
            path / "checkpoint_receipt.json",
            {
                "model": identity,
                "reload_passed": True,
                "stage": "S0_base",
                "profile": profile,
                "parent": None,
                "data_sha256": data_hash,
                "optimizer_steps": steps,
                "examples": examples,
                "adapter_sha256_before": "before",
                "adapter_sha256_after": "after",
                "files": files_manifest(path),
            },
        )
    return source, cfg, identity, data, {"job_deadline_epoch": 100000}


def test_completed_s0_reused_without_any_training(
    completed_base, tmp_path, monkeypatch
):
    source, cfg, identity, data, deployment = completed_base

    def forbidden(*args, **kwargs):
        raise AssertionError("Verified S0 must not be retrained")

    monkeypatch.setattr("recoveragent.v2_training.train_sft", forbidden)
    output = tmp_path / "next"
    result = establish_base(cfg, identity, data, output, deployment, 1, source)
    assert result == source / "S0_base"
    assert read_json(output / "continued_base.json")["reused_completed_base_training"]


@pytest.mark.parametrize(
    "damage",
    [
        "adapter_bytes",
        "missing_long",
        "partial_steps",
        "reload_failed",
        "unchanged",
        "different_model",
        "different_data",
        "different_tasks",
        "changed_recipe",
        "request_started",
        "incomplete_source",
        "deadline_reset",
        "wrong_source_lock",
    ],
)
def test_invalid_handoff_rejected(completed_base, damage):
    source, cfg, identity, data, deployment = completed_base
    cfg = copy.deepcopy(cfg)
    path = source / "S0_base/checkpoint_receipt.json"
    value = read_json(path)
    if damage == "adapter_bytes":
        (source / "S0_base/synthetic_adapter.txt").write_text("tampered")
    elif damage == "missing_long":
        (source / "smoke_long/checkpoint_receipt.json").unlink()
    elif damage == "partial_steps":
        value["optimizer_steps"] = 1
    elif damage == "reload_failed":
        value["reload_passed"] = False
    elif damage == "unchanged":
        value["adapter_sha256_after"] = value["adapter_sha256_before"]
    elif damage == "different_model":
        value["model"] = {"model": "unrelated"}
    elif damage == "different_data":
        (data / "examples.jsonl").write_text("{}\n")
    elif damage == "different_tasks":
        (source / "task_lists.json").write_text("{}")
    elif damage == "changed_recipe":
        cfg["student_limits"]["max_requests"] += 1
    elif damage == "request_started":
        (source / "feedback").mkdir()
    elif damage == "incomplete_source":
        (source / "round_summary.json").write_text(json.dumps({"status": "started"}))
    elif damage == "deadline_reset":
        deployment["job_deadline_epoch"] += 1
    elif damage == "wrong_source_lock":
        previous = read_json(source / "preflight.json")
        previous["source"]["lock_sha256"] = "0" * 64
        (source / "preflight.json").write_text(json.dumps(previous))
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, FileNotFoundError)):
        verified_base(source, cfg, identity, data, deployment)


def test_fresh_run_still_performs_both_smokes(completed_base, tmp_path, monkeypatch):
    _, cfg, identity, data, deployment = completed_base
    calls = []
    monkeypatch.setattr(
        "recoveragent.v2_training.train_sft",
        lambda *args, **kwargs: calls.append(kwargs.get("profile", "formal")),
    )
    result = establish_base(cfg, identity, data, tmp_path / "fresh", deployment, 1)
    assert calls == ["short", "long", "formal"]
    assert result == tmp_path / "fresh/S0_base"
