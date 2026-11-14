"""Synthetic upgrade selection, immutable data and S0 retraining contracts."""

import copy
from pathlib import Path
import time

import pytest

from recoveragent import p3a, teacher_escalation as escalation
from recoveragent import teacher_followup as followup
from recoveragent.demonstrations import sha256
from recoveragent.io import read_json, write_json
from recoveragent.v2_data import mix_recovery, write_rows
from recoveragent.v2_tasks import context_for, plan


def config():
    return followup.configuration(escalation.REVISION)


def test_upgraded_worker_uses_shared_executor_identity_and_full_budget(monkeypatch):
    cfg = config()
    monkeypatch.setattr(followup, "verify_lock", lambda *args: "synthetic")
    worker = followup.worker_config(
        cfg["task_ids"][0],
        "synthetic",
        "/synthetic",
        "deepseek_pro",
        revision=escalation.REVISION,
    )
    p3a.validate_config(worker)
    assert worker["teacher"]["model"] == "deepseek-v4-pro"
    assert worker["teacher"]["api_key_env"] == "DEEPSEEK_API_KEY"
    assert worker["teacher"]["base_url"] == cfg["teachers"]["deepseek_pro"]["base_url"]
    assert worker["remaining_requests"] == 80
    assert worker["remaining_generated_tokens"] == 16384
    assert worker["limits"]["episode_wall_seconds"] == 1200
    assert worker["collection_context"]["max_sequence_tokens"] == 65536
    assert not worker.get("recovery_protocol")
    altered = copy.deepcopy(worker)
    altered["teacher"]["model"] = "gpt-5.6-sol"
    with pytest.raises(ValueError, match="frozen"):
        p3a.validate_config(altered)


def make_previous(tmp_path):
    cfg = config()
    old = followup.configuration()
    root = tmp_path / "previous"
    write_json(root / "config.json", old)
    assert sha256(root / "config.json") == cfg["prior_followup"]["config_sha256"]
    write_json(
        root / "campaign.json",
        {
            "revision": cfg["prior_followup"]["revision"],
            "source_lock_sha256": cfg["prior_followup"]["source_lock_sha256"],
            "task_ids": cfg["task_ids"],
        },
    )
    write_json(root / "progress.json", {"status": "complete"})
    for i, task in enumerate(cfg["task_ids"]):
        passed = i % 2 == 0
        attempts = [{"task_id": task, "teacher": "deepseek", "eligible": passed}]
        if not passed:
            attempts.append({"task_id": task, "teacher": "gpt", "eligible": False})
        write_json(
            root / "results" / (task + ".json"),
            {
                "task_id": task,
                "eligible": passed,
                "attempts": attempts,
            },
        )
    return root


def test_upgrade_freezes_only_previous_failed_tasks_in_original_order(
    tmp_path, monkeypatch
):
    previous = make_previous(tmp_path)
    output = tmp_path / "upgraded"
    monkeypatch.setattr(escalation, "configuration", config)
    monkeypatch.setattr(followup, "verify_lock", lambda *args: "synthetic")
    escalation.prepare(output, {"previous_output": str(previous)})
    campaign = read_json(output / "campaign.json")
    assert campaign["task_ids"] == config()["task_ids"][1::2]
    assert campaign["carried_attempts"] == {}
    assert campaign["start_kind"] == "original_initial"
    escalation.verify_source_results(output)
    path = previous / "results" / (config()["task_ids"][0] + ".json")
    path.write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        escalation.verify_source_results(output)


def test_unfinished_previous_campaign_cannot_freeze_upgrade(tmp_path):
    root = make_previous(tmp_path)
    (root / "progress.json").unlink()
    write_json(root / "progress.json", {"status": "running"})
    with pytest.raises(ValueError, match="incomplete"):
        escalation.prior_results(root, config())


def test_explicit_local_handoff_keeps_successes_and_selects_failed_and_unstarted(
    tmp_path,
):
    root = make_previous(tmp_path)
    (root / "progress.json").unlink()
    write_json(root / "progress.json", {"status": "partial"})
    cfg = config()
    unstarted = cfg["task_ids"][-1]
    (root / "results" / (unstarted + ".json")).unlink()
    values = escalation.prior_results(root, cfg)
    selected = [r["task_id"] for _, r in values if not r["eligible"]]
    assert selected == cfg["task_ids"][1::2] + [unstarted]
    absent = next((path, r) for path, r in values if r["task_id"] == unstarted)
    assert absent[0] is None and absent[1]["attempts"] == []
    strict = {**cfg, "include_unstarted_after_local_handoff": False}
    with pytest.raises(ValueError, match="incomplete"):
        escalation.prior_results(root, strict)


def test_artifact_relocation_verifies_original_hash_without_editing_receipts(tmp_path):
    original = "/remote/attempt/result.json"
    local = tmp_path / "retrieved/attempt/result.json"
    write_json(local, {"unchanged": True})
    output = tmp_path / "run"
    write_json(output / "campaign.json", {"source_results": {original: sha256(local)}})
    before = (output / "campaign.json").read_bytes()
    escalation.verify_source_results(output, {"/remote": str(tmp_path / "retrieved")})
    assert (output / "campaign.json").read_bytes() == before
    local.write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        escalation.verify_source_results(
            output, {"/remote": str(tmp_path / "retrieved")}
        )


def row(task, index=0, targets=2):
    return {
        "task_id": task,
        "trajectory_id": task,
        "assistant_index": index,
        "input_ids": [1] + [2] * targets,
        "labels": [-100] + [2] * targets,
        "loss_mask": [0] + [1] * targets,
        "target_start": 1,
        "teacher_data_kind": "synthetic",
        "example_weight": 1.0,
    }


def test_replay_keeps_whole_trajectories_and_diverse_train_scenarios():
    tasks = [r["task_id"] for r in plan()["rows"] if r["role"] == "train"][:90]
    rows = [row(task, i) for task in tasks for i in range(2)]
    selected = escalation.select_replay(rows, 24)
    assert len(selected) == 48
    assert len({context_for(r["task_id"])["scenario_id"] for r in selected}) == 24
    assert {r["task_id"] for r in selected} == {
        r["task_id"] for r in escalation.select_replay(rows[::-1], 24)
    }
    with pytest.raises(ValueError, match="scope"):
        escalation.select_replay([row(plan()["dev24"][0])], 1)


def test_all_new_full_demonstrations_participate_without_truncation_or_duplication():
    tasks = config()["task_ids"]
    recovery = [row(tasks[0], targets=3)]
    normal = [row(task, targets=i + 2) for i, task in enumerate(tasks[1:5])]
    before = copy.deepcopy(recovery + normal)
    mixed = mix_recovery(recovery, normal, select_normal=False)
    assert len(mixed) == len(before)
    for actual, expected in zip(mixed, before):
        for key in ("task_id", "input_ids", "labels", "loss_mask", "assistant_index"):
            assert actual[key] == expected[key]
    for kind in ("recovery", "normal"):
        assert sum(r["example_weight"] for r in mixed if r["mix_kind"] == kind) / len(
            mixed
        ) == pytest.approx(0.5)
    assert recovery + normal == before


@pytest.mark.parametrize("bad_long", [False, True])
def test_training_loads_s0_for_both_smokes_and_formal_and_stops_on_failure(
    tmp_path, monkeypatch, bad_long
):
    from recoveragent import v2_s1_batch

    cfg = config()
    monkeypatch.setattr(escalation, "configuration", lambda: cfg)
    monkeypatch.setattr(followup, "verify_lock", lambda *args: "synthetic")
    examples = tmp_path / "training_data/examples.jsonl"
    write_rows(examples, [row(cfg["task_ids"][0])])
    write_json(
        examples.parent / "manifest.json",
        {
            "examples_sha256": sha256(examples),
            "revision": escalation.REVISION,
            "source_lock_sha256": "synthetic",
            "expected_parent": cfg["training"]["s0"],
        },
    )
    calls = []

    def checkpoint(path, conf, initial=False):
        if initial:
            assert path == "/accepted/S0"
            return {"adapter_sha256_after": "s0_hash"}
        return read_json(path / "checkpoint_receipt.json")

    def subprocess_run(command, **kwargs):
        payload = read_json(command[-1])
        assert payload["parent"] == "/accepted/S0"
        calls.append(payload["profile"])
        if payload["profile"] == "long" and bad_long:
            raise RuntimeError("synthetic OOM")
        write_json(
            Path(payload["output"]) / "checkpoint_receipt.json",
            {
                "adapter_sha256_before": "s0_hash",
                "profile": payload["profile"],
                "data_sha256": sha256(examples),
            },
        )

    monkeypatch.setattr(v2_s1_batch, "checkpoint", checkpoint)
    monkeypatch.setattr(escalation.subprocess, "run", subprocess_run)
    deployment = {
        "s0": "/accepted/S0",
        "gpu_python": "synthetic",
        "deadline_epoch": time.time() + 600,
    }
    if bad_long:
        with pytest.raises(RuntimeError, match="OOM"):
            escalation.train(tmp_path, deployment)
        assert calls == ["short", "long"]
    else:
        escalation.train(tmp_path, deployment)
        assert calls == ["short", "long", "formal"]


def test_upgraded_source_lock_and_historical_lock_separation():
    assert len(followup.verify_lock(followup.RETRY)) == 64
    with pytest.raises(ValueError, match="source|Incomplete"):
        followup.verify_lock(escalation.REVISION)
    with pytest.raises(ValueError, match="source"):
        followup.verify_lock()
