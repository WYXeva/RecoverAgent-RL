"""Verified post-S1 handoff for the approved post-S1 finish, without replay."""

import math
from pathlib import Path

from .demonstrations import sha256
from .io import digest, files_manifest, read_json, write_json
from .v2_feedback_resume import carry_feedback
from .v2_generator import accept, candidate, validate_task
from .v2_resume import completed_sft_recipe
from .v2_tasks import ROOT, plan
from .v2_training import verify_checkpoint


def carry_s1(source, cfg, identity, s0, deployment):
    source, s0 = Path(source).resolve(), Path(s0).resolve()
    previous = read_json(ROOT / "configs/v2_round1_feedback15_20261109.json")
    ignored = {"format", "source_lock", "gpu_wall_seconds"}
    if read_json(source / "run_config.json") != previous or {
        k: v for k, v in previous.items() if k not in ignored
    } != {
        k: v for k, v in completed_sft_recipe(cfg, previous).items() if k not in ignored
    }:
        raise ValueError("Post-S1 handoff changed the frozen recipe")
    preflight = read_json(source / "preflight.json")
    if (
        preflight["source"]["lock_sha256"] != sha256(ROOT / previous["source_lock"])
        or deployment["job_deadline_epoch"]
        != preflight["deployment"]["job_deadline_epoch"]
        + cfg["gpu_wall_seconds"]
        - previous["gpu_wall_seconds"]
        or cfg["gpu_wall_seconds"] not in {previous["gpu_wall_seconds"], 86400}
    ):
        raise ValueError("Post-S1 source/deadline mismatch")
    summary = read_json(source / "round_summary.json")
    if (
        summary["status"] != "stopped"
        or summary.get("evaluations")
        or summary.get("grpo")
        or (source / "rollouts").exists()
        or (source / "evaluation").exists()
        or list((source / "S2_grpo").glob("updates/*.json"))
    ):
        raise ValueError("Post-S1 source must stop before downstream requests")
    if read_json(source / "task_lists.json") != {
        "feedback20": plan()["feedback20"],
        "dev24": plan()["dev24"],
        "split_manifest_sha256": plan()["manifest_sha256"],
    }:
        raise ValueError("Post-S1 frozen task list changed")
    s1 = source / "S1_recovery"
    receipt = verify_checkpoint(s1, identity)
    parent = verify_checkpoint(s0, identity)
    handoff = read_json(source / "post_s1_handoff.json")
    if (
        handoff["reason"]
        not in {"user_approved_grpo4_dev3_32k", "user_restored_grpo16_dev24_32k"}
        or handoff["downstream_requests"] != 0
        or handoff["checkpoint_receipt_sha256"]
        != sha256(s1 / "checkpoint_receipt.json")
        or receipt["stage"] != "S1_recovery"
        or receipt["profile"] != "formal"
        or Path(receipt["parent"]).resolve() != s0
        or receipt["parent_receipt_sha256"] != sha256(s0 / "checkpoint_receipt.json")
        or receipt["adapter_sha256_before"] != parent["adapter_sha256_after"]
        or receipt["adapter_sha256_after"] == receipt["adapter_sha256_before"]
        or receipt["data_sha256"] != sha256(source / "recovery_mix.jsonl")
        or receipt["optimizer_steps"]
        != math.ceil(
            receipt["examples"] / cfg["recovery_sft"]["gradient_accumulation_steps"]
        )
        or receipt["optimizer_steps"] <= 0
        or (s1 / "training_failure.json").exists()
    ):
        raise ValueError("Post-S1 checkpoint lineage/data/update mismatch")
    continued = read_json(source / "continued_feedback.json")
    feedback, used, proof = carry_feedback(
        continued["source_run"], previous, identity, s0, preflight["deployment"]
    )
    if (
        continued != proof
        or feedback != summary["feedback"]
        or len(feedback) != cfg["feedback_tasks"]
        or used != summary["training_generated_tokens"]
        or proof.get("generation_reservation", 0)
        != summary.get("training_generated_token_reservation", 0)
        or proof.get("cancellation") != summary.get("canceled_feedback")
        or used + proof.get("generation_reservation", 0) > cfg["train_generated_tokens"]
    ):
        raise ValueError("Post-S1 feedback provenance or token ledger mismatch")
    records = []
    for item in summary["recovery"]:
        if not item["attempted"]:
            continue
        directory = Path(item["directory"])
        if directory.parent != source / "recovery":
            raise ValueError("Recovery record is outside source")
        record = read_json(directory / "trajectory.json")
        if (
            item["official_success"] != (record["reward"] == 1)
            or item["acceptance"] != read_json(directory / "suffix_acceptance.json")
            or record["status"] != "valid"
        ):
            raise ValueError("Recovery source/result changed")
        records.append(record)
    pool = read_json(source / "generated_pool.json")
    accepted = []
    for item in summary["generated"]:
        directory = Path(item["directory"])
        if directory.parent != source / "generation":
            raise ValueError("Generation record is outside source")
        records.append(read_json(directory / "trajectory.json"))
        if item["accepted"]:
            task = read_json(directory / "accepted_task.json")
            validate_task(task, task["source_task_id"])
            checks = [
                read_json(directory / mode / "validation.json")
                for mode in ("positive", "noop", "omit_completion", "wrong_answer")
            ]
            if (
                accept({k: v for k, v in task.items() if k != "validation"}, checks)
                != task
            ):
                raise ValueError("Generated task validation changed")
            accepted.append(task)
    cost = sum(
        r["usage"]["usage_priced_cost"] + r["usage"].get("unknown_cost_reservation", 0)
        for r in records
    )
    if pool != accepted or not math.isclose(
        cost, summary["teacher_usage_priced_cny"], rel_tol=1e-12, abs_tol=1e-12
    ):
        raise ValueError("Generated pool or Teacher cost ledger changed")
    carried = {
        k: summary[k]
        for k in (
            "feedback",
            "recovery",
            "generated",
            "training_generated_tokens",
            "training_generated_token_reservation",
            "teacher_usage_priced_cny",
        )
    }
    if "canceled_feedback" in summary:
        carried["canceled_feedback"] = summary["canceled_feedback"]
    return (
        carried,
        pool,
        s1,
        {
            "source_run": str(source),
            "source_summary_sha256": sha256(source / "round_summary.json"),
            "checkpoint_receipt_sha256": sha256(s1 / "checkpoint_receipt.json"),
            "source_lock_sha256": preflight["source"]["lock_sha256"],
            "source_files": {
                name: files_manifest(source / name)
                for name in ("recovery", "generation")
            },
            "job_deadline_epoch": deployment["job_deadline_epoch"],
            "no_replay": True,
        },
    )


def revalidate_public_read_proposals(
    source, output, pool, python, root, deadline, isolated
):
    """Recheck existing proposals after the public show_active_task allowlist fix.

    No new Teacher calls. Original rejections/trajectories remain immutable, and
    every accepted derived task still needs all four independent environment checks.
    """
    source, output = Path(source), Path(output)
    summary = read_json(source / "round_summary.json")
    failures = {r["task_id"]: r for r in summary["feedback"]}
    pool, audit = list(pool), []
    goals = {
        digest({k: t[k] for k in ("source_task_id", "read", "categories")})
        for t in pool
    }
    for item in summary["generated"]:
        if (
            item["accepted"]
            or item["reason"] != "Unsupported or mutating exploration action"
        ):
            continue
        original = Path(item["directory"])
        directory = output / "generation_revalidation" / original.name
        record = read_json(original / "trajectory.json")
        failure = failures[record["task_id"]]
        feedback = {
            "task_id": failure["task_id"],
            "termination": failure["termination"],
            "public_history": read_json(Path(failure["directory"]) / "history.json")[
                "messages"
            ],
        }
        try:
            task = candidate(record, feedback, original)
            goal = digest(
                {k: task[k] for k in ("source_task_id", "read", "categories")}
            )
            if goal in goals:
                raise ValueError("Duplicate generated goal")
            write_json(directory / "candidate.json", task)
            checks = []
            for mode in ("positive", "noop", "omit_completion", "wrong_answer"):
                check = directory / mode
                isolated(
                    python,
                    "generator-check",
                    {
                        "task": task,
                        "mode": mode,
                        "appworld_root": root,
                        "output": str(check),
                    },
                    check,
                    deadline,
                )
                checks.append(read_json(check / "validation.json"))
            task = accept(task, checks)
            write_json(directory / "accepted_task.json", task)
            pool.append(task)
            goals.add(goal)
            result = {"accepted": True, "task_id": task["task_id"]}
        except (ValueError, KeyError, TypeError) as exc:
            result = {"accepted": False, "reason": str(exc)}
        result.update(
            source=str(original),
            source_trajectory_sha256=sha256(original / "trajectory.json"),
            directory=str(directory),
            new_teacher_requests=0,
        )
        write_json(directory / "revalidation.json", result)
        audit.append(result)
    write_json(output / "generated_pool.json", pool)
    return pool, audit
