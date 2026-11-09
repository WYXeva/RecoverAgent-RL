"""Narrow pre-update handoff for the observed generated-answer scoring defect.

Reuses completed current-S1 samples, with separately versioned reward records.
Never edits original scores, expected answers, trajectories or source locks.
"""

import copy
import math
import shutil
from pathlib import Path

from . import coevolve_tasks as tasks
from .demonstrations import sha256
from .io import digest, files_manifest, read_json, write_json
from .v2_tasks import ROOT, context_for


def upgrade(task):
    old = copy.deepcopy(task)
    validation = old["validation"]
    if (
        old["generator"] != "coevolve_appworld_task_v1"
        or old["execution_contract"]["revision"] != old["generator"]
        or old["task_id"] != tasks.identify(old)
        or old["source_context"] != context_for(old["source_task_id"], training=True)
        or not validation["passed"]
        or validation["sha256"] != digest(validation["results"])
    ):
        raise ValueError("Legacy generated task identity/validation mismatch")
    tasks.check_results(old, validation["results"])
    new = {k: v for k, v in old.items() if k != "validation"}
    new["generator"] = tasks.VERSION
    new["execution_contract"]["revision"] = tasks.VERSION
    new["execution_contract"]["answer_comparison"] = tasks.answer_comparison(
        new["instruction"]
    )
    new["task_id"] = tasks.identify(new)
    return new


def verify_source(source, cfg, deployment, s1, carried):
    source = Path(source).resolve()
    old = read_json(ROOT / "configs/v2_round1_coevolve_20261109.json")
    normalized = copy.deepcopy(cfg)
    if normalized["task_evolution"].pop("validation_processes", None) != 2:
        raise ValueError("Unsupported validation worker count")
    for name in ("format", "source_lock", "generator"):
        normalized[name] = old[name]
    if normalized != old or read_json(source / "run_config.json") != old:
        raise ValueError("Generated-answer handoff changed the learning recipe")
    preflight = read_json(source / "preflight.json")
    lock = read_json(ROOT / old["source_lock"])
    old_code = Path(deployment["coevolve_source_directory"]).resolve()
    if (
        preflight["source"]["lock_sha256"] != sha256(ROOT / old["source_lock"])
        or sha256(old_code / old["source_lock"]) != preflight["source"]["lock_sha256"]
        or any(
            sha256(old_code / name) != expected
            for name, expected in lock["files"].items()
        )
        or preflight["deployment"]["job_deadline_epoch"]
        != deployment["job_deadline_epoch"]
    ):
        raise ValueError("Generated-answer source lock/deadline mismatch")
    summary = read_json(source / "round_summary.json")
    handoff = read_json(source / "answer_contract_handoff.json")
    if (
        summary["status"] != "stopped"
        or summary.get("grpo")
        or summary.get("evaluations")
        or list((source / "S2_grpo").rglob("*.safetensors"))
        or list((source / "S2_grpo/updates").glob("*.json"))
        or handoff["reason"]
        != "generated_count_answer_string_vs_number_verifier_bug_before_first_GRPO_update"
        or handoff["optimizer_steps_before_stop"] != 0
        or handoff["absolute_deadline"] != deployment["job_deadline_epoch"]
        or summary["feedback"] != carried["feedback"]
        or summary["recovery"] != carried["recovery"]
        or Path(summary["real_checkpoints"]["S1_recovery"]).resolve()
        != Path(s1).resolve()
    ):
        raise ValueError("Source must stop before any GRPO update/evaluation")
    receipt = read_json(Path(s1) / "checkpoint_receipt.json")
    policy = {
        **receipt["model"],
        "checkpoint_id": digest({"adapter_sha256": receipt["adapter_sha256_after"]}),
    }
    completed, generated = [], 0
    for directory in sorted((source / "rollouts").iterdir()):
        if not directory.is_dir():
            continue
        requests = sorted((directory / "session/requests").glob("*.request.json"))
        if not (directory / "result.json").exists():
            if requests:
                raise ValueError("Partial generated-answer rollout; no silent replay")
            continue
        result = read_json(directory / "result.json")
        if (
            result["policy"] != policy
            or result["classification"] == "infrastructure_error"
            or result["purpose"] != "grpo"
            or result["generated_task_id"] is None
            or directory.name != f"group_00_member_{len(completed)}"
        ):
            raise ValueError("Expected an unchanged-S1 generated group prefix")
        amount = 0
        for request in requests:
            response = request.with_name(
                request.name.replace(".request.json", ".response.json")
            )
            q, a = read_json(request), read_json(response)
            if (
                q["policy"] != policy
                or a["policy"] != policy
                or q["input_token_ids"] != a["input_token_ids"]
                or len(a["token_ids"]) != len(a["logprobs"])
            ):
                raise ValueError("Behavior receipt mismatch")
            amount += len(a["token_ids"])
        if amount != result["usage"]["generated_tokens"]:
            raise ValueError("Generated-answer rollout token mismatch")
        generated += amount
        completed.append((directory, result))
    if (
        not 1 <= len(completed) <= 4
        or summary["training_generated_tokens"]
        != carried["training_generated_tokens"] + generated
        or summary["training_generated_token_reservation"]
        != carried["training_generated_token_reservation"]
    ):
        raise ValueError("Generated-answer training ledger mismatch")
    records = [read_json(p) for p in (source / "coevolve").rglob("trajectory.json")]
    text = [read_json(p) for p in (source / "coevolve").rglob("usage.json")]
    known = sum(
        r["usage"]["usage_priced_cost"] + r["usage"].get("unknown_cost_reservation", 0)
        for r in records
    ) + sum(r["usage_priced_cost"] for r in text)
    if list((source / "coevolve").rglob("unknown_usage.json")) or not math.isclose(
        summary["teacher_usage_priced_cny"],
        carried["teacher_usage_priced_cny"] + known,
        abs_tol=1e-10,
    ):
        raise ValueError("Generated-answer Teacher cost provenance mismatch")
    return summary, completed


def resume(evolution, source, s1, ledger):
    source = Path(source).resolve()
    summary, completed = verify_source(
        source, evolution.cfg, evolution.deployment, s1, evolution.summary
    )
    snapshot = read_json(source / "coevolve/pool_after_cycle_01.json")
    if (
        snapshot["attempts"] != summary["coevolve"]["attempts"]
        or len(snapshot["pool"]) != summary["coevolve"]["accepted"]
    ):
        raise ValueError("Generated pool snapshot mismatch")
    evolution.signals.observe(evolution.summary["feedback"])
    if evolution.signals.export() != snapshot["signals"]:
        raise ValueError("Saved feedback signals differ from their original evidence")
    mapping = {}
    for i, old in enumerate(snapshot["pool"]):
        task = upgrade(old)
        item = next(
            a for a in snapshot["attempts"] if a.get("task_id") == old["task_id"]
        )
        prior = Path(item["directory"])
        reference = prior / (prior.name + "_solver") / "trajectory.json"
        if (
            read_json(prior / "accepted_task.json") != old
            or digest(read_json(reference))
            != task["execution_contract"]["reference_trajectory_sha256"]
        ):
            raise ValueError("Generated reference source changed")
        target = evolution.output / f"revalidate_{i:02d}"
        write_json(target / "source_task.json", old)
        write_json(target / "candidate.json", task)
        checks = evolution.checks(task, reference, target)
        task = tasks.accept(task, checks)
        write_json(target / "accepted_task.json", task)
        evolution.pool.append(task)
        mapping[old["task_id"]] = task
    rewards = {}
    for member, (directory, original) in enumerate(completed):
        task = mapping[original["generated_task_id"]]
        target = evolution.output / f"rescore_member_{member}"
        evolution.isolated(
            evolution.deployment["worker_python"],
            "generator-rescore",
            {
                "task": task,
                "result": str(directory / "result.json"),
                "result_sha256": sha256(directory / "result.json"),
                "appworld_root": evolution.deployment["appworld_root"],
                "output": str(target),
            },
            target,
            evolution.deadline,
        )
        derived = read_json(target / "derived_result.json")
        rewards[(0, member)] = {**derived, "directory": str(directory)}
    evolution.attempts = snapshot["attempts"]
    evolution.cycle = summary["coevolve"]["cycles"]
    evolution.consumed_events = {
        read_json(p)["event_id"]
        for p in (source / "coevolve").glob("cycle_*/signal.json")
    }
    evolution.summary["teacher_usage_priced_cny"] = summary["teacher_usage_priced_cny"]
    ledger["used"] = summary["training_generated_tokens"]
    write_json(
        evolution.output / "continued_generation.json",
        {
            "source_run": str(source),
            "source_summary_sha256": sha256(source / "round_summary.json"),
            "source_generation_files": files_manifest(source / "coevolve"),
            "source_rollout_files": files_manifest(source / "rollouts"),
            "task_revision_mapping": {k: v["task_id"] for k, v in mapping.items()},
            "reused_current_S1_episodes": len(rewards),
            "previous_optimizer_steps": 0,
            "known_training_generated_tokens": ledger["used"],
            "new_model_requests_for_revalidation": 0,
        },
    )
    evolution.snapshot("continued_pool.json")
    return rewards


def rescore(payload):
    from .adapter import AppWorldAdapter
    from .schema_compatibility import SCHEMA_REVISION
    from .runner import now

    started = now()

    path, output = Path(payload["result"]), Path(payload["output"])
    if sha256(path) != payload["result_sha256"]:
        raise ValueError("Source rollout changed")
    original, task = read_json(path), payload["task"]
    tasks.validate_task(task, original["task_id"])
    if (
        original["initial_state_hash"]
        != task["execution_contract"]["initial_state_hash"]
    ):
        raise ValueError("Rescored rollout initial state changed")
    source = (
        Path(payload["appworld_root"])
        / "experiments/outputs"
        / original["experiment_name"]
        / "tasks"
        / original["task_id"]
        / "dbs"
    )
    manifest = files_manifest(source)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source, output / "observed_terminal")
    adapter = AppWorldAdapter(
        payload["appworld_root"],
        original["task_id"],
        "rescore_" + digest(str(output))[:16],
        research=context_for(original["task_id"], training=True),
    )
    try:
        adapter.configure_schema_compatibility(SCHEMA_REVISION, output)
        adapter.import_state(output / "observed_terminal", "original_observed_terminal")
        evaluated = tasks.evaluate(adapter, task)
        derived = {
            **original,
            "generated_task_id": task["task_id"],
            "evaluation": evaluated,
            "success": evaluated["success"],
            "classification": "success" if evaluated["success"] else "natural_failure",
        }
        write_json(output / "derived_result.json", derived)
        write_json(
            output / "rescore_provenance.json",
            {
                "original_result_sha256": sha256(path),
                "original_reward": original["success"],
                "original_generated_task_id": original["generated_task_id"],
                "source_terminal_files": manifest,
                "control_only": True,
                "model_requests": 0,
                "source_result_unchanged": True,
                "wall_seconds": now() - started,
            },
        )
    finally:
        adapter.close()
    if files_manifest(source) != manifest:
        raise ValueError("Source terminal changed during read-only rescore")
