"""Continue after a verified S0, before any downstream model requests."""

import math
from pathlib import Path

from .demonstrations import sha256
from .io import read_json, write_json
from .v2_tasks import ROOT, plan


def verified_base(source, cfg, identity, data, deployment):
    from .v2_training import verify_checkpoint

    source = Path(source).resolve()
    previous = read_json(source / "run_config.json")
    ignored = {"format", "source_lock", "gpu_wall_seconds"}
    if {k: v for k, v in previous.items() if k not in ignored} != {
        k: v for k, v in cfg.items() if k not in ignored
    }:
        raise ValueError("S0 continuation changed the frozen learning recipe")
    expected = read_json(ROOT / "configs/v2_round1_cpu_offload_20261109.json")
    if previous != expected:
        raise ValueError("Unsupported S0 source configuration")
    preflight = read_json(source / "preflight.json")
    if preflight["source"]["lock_sha256"] != sha256(ROOT / previous["source_lock"]):
        raise ValueError("S0 source lock provenance mismatch")
    maximum_deadline = (
        preflight["deployment"]["job_deadline_epoch"]
        + cfg["gpu_wall_seconds"]
        - previous["gpu_wall_seconds"]
    )
    if deployment["job_deadline_epoch"] > maximum_deadline:
        raise ValueError("Continuation must not restart the GPU time budget")
    stopped = read_json(source / "round_summary.json")
    if stopped["status"] != "stopped" or any(
        stopped.get(key) for key in ("feedback", "recovery", "generated", "evaluations")
    ):
        raise ValueError("Continuation requires a stopped run before feedback")
    if stopped.get("training_generated_tokens", 0) or any(
        (source / key).exists()
        for key in ("feedback", "recovery", "generation", "rollouts", "evaluation")
    ):
        raise ValueError("Downstream requests already started; refusing replay")
    if read_json(source / "task_lists.json") != {
        "feedback20": plan()["feedback20"],
        "dev24": plan()["dev24"],
        "split_manifest_sha256": plan()["manifest_sha256"],
    }:
        raise ValueError("S0 task lists or scenario split mismatch")
    prepared = read_json(Path(data) / "preparation.json")
    data_hash = sha256(Path(data) / "examples.jsonl")
    if prepared["model"] != identity or prepared["examples_sha256"] != data_hash:
        raise ValueError("Prepared S0 model/data identity mismatch")
    receipts = {}
    for stage, profile in (
        ("smoke_short", "short"),
        ("smoke_long", "long"),
        ("S0_base", "formal"),
    ):
        receipt = verify_checkpoint(source / stage, identity)
        steps = {"short": 2, "long": 1}.get(profile)
        if steps is None:
            steps = math.ceil(
                prepared["examples"] / cfg["base_sft"]["gradient_accumulation_steps"]
            )
        if (
            receipt["stage"] != "S0_base"
            or receipt["profile"] != profile
            or receipt["parent"] is not None
            or receipt["data_sha256"] != data_hash
            or receipt["optimizer_steps"] != steps
            or (profile == "formal" and receipt["examples"] != prepared["examples"])
            or (profile == "short" and not 1 <= receipt["examples"] <= 4)
            or (profile == "long" and receipt["examples"] != 1)
            or receipt["adapter_sha256_before"] == receipt["adapter_sha256_after"]
            or (source / stage / "training_failure.json").exists()
        ):
            raise ValueError("S0 or smoke acceptance evidence mismatch")
        receipts[stage] = sha256(source / stage / "checkpoint_receipt.json")
    return source / "S0_base", {
        "source_run": str(source),
        "source_lock_sha256": preflight["source"]["lock_sha256"],
        "checkpoint_receipt_sha256": receipts,
        "data_sha256": data_hash,
        "job_deadline_epoch": deployment["job_deadline_epoch"],
        "reused_completed_base_training": True,
        "downstream_requests_before_continuation": 0,
    }


def establish_base(cfg, identity, data, output, deployment, deadline, source=None):
    from .v2_training import train_sft

    if source is not None:
        checkpoint, evidence = verified_base(source, cfg, identity, data, deployment)
        write_json(Path(output) / "continued_base.json", evidence)
        return checkpoint
    for profile in ("short", "long"):
        train_sft(
            cfg,
            identity,
            Path(data) / "examples.jsonl",
            Path(output) / ("smoke_" + profile),
            profile=profile,
            deadline=deadline,
        )
    checkpoint = Path(output) / "S0_base"
    train_sft(
        cfg, identity, Path(data) / "examples.jsonl", checkpoint, deadline=deadline
    )
    return checkpoint
