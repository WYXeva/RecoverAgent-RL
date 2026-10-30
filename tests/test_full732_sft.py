"""CPU software checks for masks, isolation, weights and fail-closed stage order."""

import ast
import copy
import math
from collections import defaultdict

import pytest

from recoveragent import full732_sft_data as data
from recoveragent import full732_sft_runtime as runtime
from recoveragent.io import read_json
from recoveragent.io import digest, files_manifest, write_json


def synthetic():
    accepted, examples = [], []
    for tid, count in [("one", 1), ("three", 3)]:
        source = {
            "trajectory_id": tid,
            "task_id": tid + "_1",
            "scenario_id": tid,
            "source_demo_sha256": tid,
            "disposition": "NATIVE",
            "native": {"assistant_samples": count},
        }
        accepted.append(source)
        for index in range(count):
            examples.append(
                {
                    **{
                        k: source[k]
                        for k in (
                            "trajectory_id",
                            "task_id",
                            "scenario_id",
                            "source_demo_sha256",
                        )
                    },
                    "assistant_index": index,
                    "input_ids": [10, 20, 30],
                    "target_start": 2,
                    "labels": [-100, -100, 30],
                    "loss_mask": [0, 0, 1],
                    "attention_mask": [1, 1, 1],
                }
            )
    return accepted, examples


def test_trajectory_balancing_preserves_targets_and_mean_one():
    accepted, examples = synthetic()
    before = copy.deepcopy(examples)
    result = list(data.weighted_rows(examples, accepted))
    mass = defaultdict(float)
    for row in result:
        mass[row["trajectory_id"]] += row["example_weight"]
        weight = row.pop("example_weight")
        assert math.isfinite(weight) and weight > 0
    assert result == examples == before
    assert mass == {"one": 2, "three": 2}


@pytest.mark.parametrize(
    "damage",
    ["labels", "mask", "missing", "extra", "duplicate", "outside_A", "overlength"],
)
def test_bad_training_rows_are_rejected(damage):
    accepted, examples = synthetic()
    if damage == "labels":
        examples[0]["labels"][0] = 10
    elif damage == "mask":
        examples[0]["loss_mask"][0] = 1
    elif damage == "missing":
        examples.pop()
    elif damage in {"extra", "duplicate"}:
        examples.append(copy.deepcopy(examples[-1]))
        if damage == "extra":
            examples[-1]["assistant_index"] = 100
    elif damage == "outside_A":
        examples[0]["task_id"] = "C_task"
    else:
        examples[0]["input_ids"] = [1] * 32769
    with pytest.raises(ValueError):
        list(data.weighted_rows(examples, accepted))


def test_full_split_membership_rejects_B_C_before_data_loading():
    plan = read_json(data.ROOT / "configs/unified_task_split_full_pool_candidate.json")
    # Only public IDs; no private Teacher data or task content read in this test.
    for role in ("recovery_grpo", "shared_validation"):
        row = next(r for r in plan["rows"] if r["role"] == role)
        with pytest.raises(ValueError, match="outside frozen A244"):
            data.verify_roles(plan, [row])


@pytest.mark.parametrize(
    "profile,steps,accum",
    [("short_smoke", 2, 2), ("long_smoke", 1, 1), ("formal", -1, 8)],
)
def test_frozen_recipes_disable_validation(profile, steps, accum):
    config = read_json(data.ROOT / "configs" / f"full732_m1_{profile}.json")
    runtime.validate_config(config)
    args = config["training_args"]
    assert args["eval_strategy"] == "no" and args["do_eval"] is False
    assert args["max_steps"] == steps and args["gradient_accumulation_steps"] == accum
    assert args["num_train_epochs"] == 1
    config["training_args"]["eval_strategy"] = "steps"
    with pytest.raises(ValueError):
        runtime.validate_config(config)


def test_complete_longest_selection_and_formal_all_examples():
    rows = [{"input_ids": [1] * n} for n in (100, 32693, 256, 1000, 2000)]
    for profile, expected in [
        ("short_smoke", [100, 256, 1000, 2000]),
        ("long_smoke", [32693]),
    ]:
        config = read_json(data.ROOT / "configs" / f"full732_m1_{profile}.json")
        chosen = runtime.select_examples(rows, config)
        assert [len(r["input_ids"]) for r in chosen] == expected
        assert all(any(r is original for original in rows) for r in chosen)
    assert runtime.select_examples(rows, {"profile": "formal"}) is rows


@pytest.mark.parametrize("profile", ["long_smoke", "formal"])
def test_no_direct_formal_or_long_launch_without_smoke_receipts(profile):
    with pytest.raises(ValueError, match="preceding smoke"):
        runtime.check_prerequisites(profile, [], "prepared", "lock")


def test_runtime_has_no_evaluation_dataset_or_evaluate_call():
    tree = ast.parse(
        (data.ROOT / "src/recoveragent/full732_sft_runtime.py").read_text()
    )
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert not any(
        isinstance(n.func, ast.Attribute) and n.func.attr == "evaluate" for n in calls
    )
    trainer = next(
        n
        for n in calls
        if isinstance(n.func, ast.Name) and n.func.id == "WeightedTrainer"
    )
    assert isinstance(
        next(k.value for k in trainer.keywords if k.arg == "eval_dataset"), ast.Constant
    )
    assert (
        next(k.value.value for k in trainer.keywords if k.arg == "eval_dataset") is None
    )


def test_preparation_cannot_write_inside_immutable_package(tmp_path):
    with pytest.raises(ValueError, match="original data package"):
        data.prepare(tmp_path, tmp_path / "archive", tmp_path / "derived")


@pytest.mark.parametrize(
    "damage", ["data", "reload", "unchanged", "artifact", "config"]
)
def test_invalid_smoke_receipt_never_unlocks_next_stage(tmp_path, damage):
    config = read_json(data.ROOT / "configs/full732_m1_short_smoke.json")
    (tmp_path / "adapter.synthetic").write_text("synthetic fixture; not model weights")
    receipt = {
        "status": "PASS",
        "profile": "short_smoke",
        "runtime_contract": data.FORMAT,
        "source_manifest_sha256": data.MANIFEST,
        "prepared_manifest_sha256": "prepared",
        "runtime_lock_sha256": "lock",
        "config_sha256": digest(config),
        "checkpoint_reloaded": True,
        "parameter_update_and_reload_pass": True,
        "optimizer_steps": 2,
        "trainable_before_hash": "before",
        "trainable_after_hash": "after",
        "restored_trainable_hash": "after",
        "files": files_manifest(tmp_path),
    }
    if damage == "data":
        receipt["prepared_manifest_sha256"] = "another_dataset"
    elif damage == "reload":
        receipt["checkpoint_reloaded"] = False
    elif damage == "unchanged":
        receipt["trainable_before_hash"] = "after"
    elif damage == "config":
        receipt["config_sha256"] = "different_recipe"
    else:
        (tmp_path / "adapter.synthetic").write_text("changed checkpoint")
    path = tmp_path / "recoveragent_checkpoint.json"
    write_json(path, receipt)
    with pytest.raises(ValueError):
        runtime.check_prerequisites("long_smoke", [path], "prepared", "lock")


def test_driver_stops_after_failed_short_smoke(tmp_path, monkeypatch):
    from scripts import run_full732_m1_training as driver
    from types import SimpleNamespace

    output = tmp_path / "output"
    monkeypatch.setattr(
        "sys.argv",
        [
            "driver",
            "--dataset",
            str(tmp_path / "source"),
            "--archive",
            str(tmp_path / "archive"),
            "--model-cache",
            str(tmp_path / "cache"),
            "--appworld-python",
            "synthetic",
            "--output",
            str(output),
        ],
    )
    monkeypatch.setattr(driver, "verify_lock", lambda: "lock")
    monkeypatch.setattr(
        driver.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="{}")
    )
    monkeypatch.setattr(
        driver,
        "prepare",
        lambda source, archive, target: write_json(target / "manifest.json", {}),
    )
    stages = []

    def fail(command, *args):
        stages.append(command)
        raise RuntimeError("synthetic training failure")

    monkeypatch.setattr(driver, "run_stage", fail)
    # Restore environment variables changed by the CLI after this software test.
    monkeypatch.setattr(driver.os, "environ", dict(driver.os.environ))
    with pytest.raises(RuntimeError, match="synthetic training failure"):
        driver.main()
    summary = read_json(output / "summary.json")
    assert len(stages) == 1 and summary["status"] == "STOPPED"
    assert summary["unfinished_stage"] == "short_smoke"
    assert summary["training_started"] is False
    assert not (output / "formal_command.json").exists()
