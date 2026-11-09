"""Continue after a verified S0, before any downstream model requests."""

import math
from pathlib import Path

from .demonstrations import sha256
from .io import digest, files_manifest, read_json, write_json
from .v2_tasks import ROOT, plan


def feedback_recipe(cfg, previous):
    """User-approved first15 prefix; all other recipe fields remain comparable."""
    cfg = dict(cfg)
    if cfg.get("feedback_tasks") == 15 and previous.get("feedback_tasks") == 20:
        cfg["feedback_tasks"] = 20
    return cfg


def verified_base(source, cfg, identity, data, deployment):
    from .v2_training import verify_checkpoint

    source = Path(source).resolve()
    previous = read_json(source / "run_config.json")
    ignored = {"format", "source_lock", "gpu_wall_seconds"}
    if {k: v for k, v in previous.items() if k not in ignored} != {
        k: v for k, v in feedback_recipe(cfg, previous).items() if k not in ignored
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


def carry_clock_interruption(source, cfg, identity, s0, deployment):
    """Keep completed feedback within its real budget; restart only zero-request setup."""
    source = Path(source).resolve()
    previous = read_json(ROOT / "configs/v2_round1_resume12h_20261109.json")
    ignored = {"format", "source_lock"}
    if read_json(source / "run_config.json") != previous or {
        k: v for k, v in previous.items() if k not in ignored
    } != {k: v for k, v in feedback_recipe(cfg, previous).items() if k not in ignored}:
        raise ValueError("Clock continuation changed the experiment")
    preflight = read_json(source / "preflight.json")
    if (
        preflight["source"]["lock_sha256"] != sha256(ROOT / previous["source_lock"])
        or deployment["job_deadline_epoch"]
        != preflight["deployment"]["job_deadline_epoch"]
    ):
        raise ValueError("Clock continuation source/deadline mismatch")
    summary = read_json(source / "round_summary.json")
    if (
        summary["status"] != "stopped"
        or len(summary["feedback"]) != 1
        or any(
            summary.get(key)
            for key in (
                "recovery",
                "generated",
                "evaluations",
                "teacher_usage_priced_cny",
            )
        )
        or any(
            (source / key).exists()
            for key in ("recovery", "generation", "rollouts", "evaluation")
        )
    ):
        raise ValueError(
            "Expected one completed feedback task before any Teacher request"
        )
    continued = read_json(source / "continued_base.json")
    if continued["checkpoint_receipt_sha256"]["S0_base"] != sha256(
        Path(s0) / "checkpoint_receipt.json"
    ):
        raise ValueError("Clock handoff changed S0")
    frozen = plan()
    if read_json(source / "task_lists.json") != {
        "feedback20": frozen["feedback20"],
        "dev24": frozen["dev24"],
        "split_manifest_sha256": frozen["manifest_sha256"],
    }:
        raise ValueError("Clock handoff changed task lists")
    task = frozen["feedback20"][0]
    directory = source / "feedback" / f"feedback_00_{task}"
    second = source / "feedback" / f"feedback_01_{frozen['feedback20'][1]}"
    directories = sorted(p for p in (source / "feedback").iterdir() if p.is_dir())
    if directories not in ([directory], [directory, second]) or (
        second.exists()
        and (
            (second / "result.json").exists()
            or list((second / "session/requests").glob("*.json"))
        )
    ):
        raise ValueError("Only an unrequested second setup may be restarted")
    control = read_json(source / "controller_interruption.json")
    if (
        control["reason_code"] != "appworld_frozen_monotonic"
        or control["retry_completed_task"] is not False
        or control["source_summary_sha256"] != sha256(source / "round_summary.json")
        or control["feedback_files"] != files_manifest(source / "feedback")
        or not 0
        <= control["completed_task_controller_wall_seconds"]
        <= cfg["student_limits"]["wall_seconds"]
        or control["completed_result_epoch"] > control["pause_epoch"]
    ):
        raise ValueError("Completed feedback/time provenance changed")
    legacy = Path(control["teacher_source_directory"])
    if sha256(legacy / previous["source_lock"]) != preflight["source"]["lock_sha256"]:
        raise ValueError("Original Teacher runtime lock mismatch")
    result = read_json(directory / "result.json")
    if summary["feedback"][0] != {**result, "directory": str(directory)}:
        raise ValueError("Completed feedback summary differs from original result")
    if (
        result["classification"] not in {"success", "natural_failure"}
        or result["evaluation"]["status"] != "valid"
        or not 0 <= result["wall_seconds"] <= 1200
    ):
        raise ValueError("Completed feedback has no valid bounded official result")
    adapter_hash = read_json(Path(s0) / "checkpoint_receipt.json")[
        "adapter_sha256_after"
    ]
    policy = {**identity, "checkpoint_id": digest({"adapter_sha256": adapter_hash})}
    episode_config = read_json(directory.with_name(directory.name + ".config.json"))
    if (
        episode_config["task_id"] != task
        or episode_config["purpose"] != "feedback"
        or episode_config["model"] != identity
        or episode_config["checkpoint_id"] != policy["checkpoint_id"]
        or episode_config["student_limits"] != cfg["student_limits"]
        or episode_config["output"] != str(directory)
        or result["policy"] != policy
    ):
        raise ValueError("Completed task identity/budget changed")
    request_dir = directory / "session/requests"
    requests = sorted(request_dir.glob("*.request.json"))
    if not 2 <= len(requests) <= cfg["student_limits"]["max_requests"]:
        raise ValueError("Invalid real request count")
    tokens = 0
    for path in requests:
        request = read_json(path)
        response = read_json(
            path.with_name(path.name.replace(".request.json", ".response.json"))
        )
        if (
            request["policy"] != policy
            or response["policy"] != policy
            or request["remaining_wall_seconds"] != 1200
        ):
            raise ValueError("Completed policy/clock evidence mismatch")
        tokens += len(response["token_ids"])
    if (
        len(requests) != len(list(request_dir.glob("*.response.json")))
        or tokens != result["usage"]["generated_tokens"]
        or tokens != summary["training_generated_tokens"]
        or result["usage"]["unknown_generation_reservation"]
        or tokens > cfg["student_limits"]["max_generated_tokens"]
    ):
        raise ValueError("Completed response accounting mismatch")
    return (
        {
            **result,
            "directory": str(directory),
            "teacher_source_directory": str(legacy),
            "teacher_source_lock_sha256": preflight["source"]["lock_sha256"],
            "controller_interruption_sha256": sha256(
                source / "controller_interruption.json"
            ),
            "clock_metadata_invalid": True,
            "actual_wall_budget_verified": True,
            "retry_performed": False,
        },
        tokens,
        0,
    )
