"""One bounded learning round, with private create-once evidence at each stage."""

import argparse
import gc
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

from .io import digest, read_json, write_json
from .v2_tasks import ROOT, plan


def config():
    return read_json(ROOT / "configs/v2_round1_clock_20261109.json")


def isolated(python, command, payload, destination, deadline, *, env=None):
    destination = Path(destination)
    request = destination.parent / (destination.name + ".input.json")
    write_json(request, payload)
    with (destination.parent / (destination.name + ".log")).open("x") as log:
        subprocess.run(
            [python, "-m", "recoveragent.v2_round", command, "--input", str(request)],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            env=env,
            timeout=max(1, min(1300, deadline - time.monotonic())),
        )


def prepare(output, identity):
    from .v2_data import prepare_base
    from .v2_model import load_tokenizer

    return prepare_base(
        ROOT / "data/full732_teacher_successes_complete_20261029",
        output,
        load_tokenizer(identity, local_files_only=False),
        identity,
        target=config()["base_trajectories"],
    )


def preflight(deployment, identity):
    import torch
    from .teacher import preflight as teacher_preflight, read_credentials
    from .v2_teacher import configuration
    from .v2_runtime import verify_lock

    verify_lock()
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise ValueError("One available GPU is required")
    props = torch.cuda.get_device_properties(0)
    if props.total_memory < 28 * 1024**3 or not torch.cuda.is_bf16_supported():
        raise ValueError("GPU cannot hold this BF16 recipe; no quantized fallback")
    teacher = configuration(
        plan()["feedback20"][0],
        identity,
        deployment["appworld_root"],
        deployment["teacher_budget_cny"],
    )
    check = teacher_preflight(teacher, read_credentials(ROOT / ".env"))
    if check["status"] == "BLOCKED":
        raise ValueError(
            "Teacher configuration unavailable: " + ",".join(check["reasons"])
        )
    return {
        "gpu": props.name,
        "memory_bytes": props.total_memory,
        "teacher": check,
        "deployment": deployment,
        "source": verify_lock(),
    }


def run(deployment, output, data, *, resume_base_from=None, carry_clock_from=None):
    import torch
    from peft import PeftModel
    from .v2_data import read_rows, write_rows, prepare_recovery, mix_recovery
    from .v2_generator import candidate, accept
    from .v2_inference import episode, policy_id
    from .v2_model import load_base, load_tokenizer
    from .v2_teacher import configuration
    from .v2_training import train_sft, verify_checkpoint
    from .v2_grpo import train as train_grpo
    from .v2_resume import establish_base, carry_clock_interruption

    cfg = config()
    identity = cfg["models"][cfg["selected_model"]]
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "run_config.json", cfg)
    write_json(
        output / "task_lists.json",
        {
            "feedback20": plan()["feedback20"],
            "dev24": plan()["dev24"],
            "split_manifest_sha256": plan()["manifest_sha256"],
        },
    )
    summary = {
        "status": "started",
        "real_checkpoints": {},
        "feedback": [],
        "recovery": [],
        "generated": [],
        "evaluations": {},
        "teacher_usage_priced_cny": 0.0,
        "training_generated_tokens": 0,
    }
    started = time.monotonic()
    remaining_job_seconds = min(
        cfg["gpu_wall_seconds"],
        deployment.get("job_deadline_epoch", time.time() + cfg["gpu_wall_seconds"])
        - time.time(),
    )
    if remaining_job_seconds <= 0:
        raise TimeoutError("Deployment GPU deadline has already expired")
    deadline = started + remaining_job_seconds
    ledger = {"used": 0, "limit": cfg["train_generated_tokens"]}
    python = deployment.get("worker_python", sys.executable)

    def alarm(*_):
        raise TimeoutError("GPU run reached configured absolute deadline")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, remaining_job_seconds)
    try:
        write_json(output / "preflight.json", preflight(deployment, identity))
        tokenizer = load_tokenizer(identity)
        prepared = read_json(Path(data) / "preparation.json")
        from .demonstrations import sha256

        if prepared["model"] != identity or prepared["examples_sha256"] != sha256(
            Path(data) / "examples.jsonl"
        ):
            raise ValueError("Prepared model/data identity mismatch")
        s0 = establish_base(
            cfg, identity, data, output, deployment, deadline, source=resume_base_from
        )
        summary["real_checkpoints"]["S0_base"] = str(s0)
        if carry_clock_from is not None:
            carried, known, reserved = carry_clock_interruption(
                carry_clock_from, cfg, identity, s0, deployment
            )
            summary["feedback"].append(carried)
            summary["training_generated_token_reservation"] = reserved
            ledger.update(used=known, limit=cfg["train_generated_tokens"] - reserved)
            write_json(output / "carried_clock_interruption.json", carried)

        def load_policy(path):
            verify_checkpoint(path, identity)
            return PeftModel.from_pretrained(
                load_base(identity), str(path), is_trainable=False
            )

        def run_student(
            model, checkpoint_id, task, directory, purpose, seed=42, generated=None
        ):
            request = {
                "task_id": task,
                "model": identity,
                "checkpoint_id": checkpoint_id,
                "purpose": purpose,
                "output": str(directory),
                "appworld_root": deployment["appworld_root"],
                "student_limits": {**cfg["student_limits"], "seed": seed},
            }
            if generated:
                request["generated_task"] = generated
            result = episode(
                request,
                model,
                tokenizer,
                python=python,
                deadline=deadline,
                token_ledger=ledger if purpose != "evaluation" else None,
            )
            return {**result, "directory": str(directory)}

        model = load_policy(s0)
        feedback_start = len(summary["feedback"])
        for i, task in enumerate(plan()["feedback20"]):
            if i < feedback_start:
                continue
            result = run_student(
                model,
                policy_id(model),
                task,
                output / "feedback" / f"feedback_{i:02d}_{task}",
                "feedback",
            )
            summary["feedback"].append(result)
            if result["classification"] == "infrastructure_error":
                raise RuntimeError(
                    "Feedback infrastructure failure; retained, no paid retry"
                )
        del model
        gc.collect()
        torch.cuda.empty_cache()

        def teacher(task, directory, *, source=None, exploration=None, legacy=None):
            remaining = (
                deployment["teacher_budget_cny"] - summary["teacher_usage_priced_cny"]
            )
            if remaining <= 0:
                raise RuntimeError("Shared Teacher money cap exhausted")
            teacher_config = configuration(
                task,
                identity,
                deployment["appworld_root"],
                remaining,
                recovery=source is not None,
            )
            worker_env = None
            if legacy:
                from .v2_legacy_teacher import call, environment

                teacher_config = call(
                    python,
                    legacy["teacher_source_directory"],
                    legacy["teacher_source_lock_sha256"],
                    "configuration",
                    {
                        "task_id": task,
                        "identity": identity,
                        "root": deployment["appworld_root"],
                        "remaining_cost": remaining,
                        "recovery": True,
                    },
                )
                worker_env = environment(legacy["teacher_source_directory"])
            payload = {
                "config": teacher_config,
                "task_id": task,
                "output": str(directory),
                "source": source,
                "exploration": exploration,
            }
            isolated(
                python, "teacher-worker", payload, directory, deadline, env=worker_env
            )
            record = read_json(directory / "trajectory.json")
            summary["teacher_usage_priced_cny"] += record["usage"][
                "usage_priced_cost"
            ] + record["usage"].get("unknown_cost_reservation", 0)
            if record["status"] != "valid":
                raise RuntimeError(
                    "Teacher infrastructure exception; no automatic paid retry"
                )
            return record

        recovery_rows = []
        failures = [
            r for r in summary["feedback"] if r["classification"] == "natural_failure"
        ]
        for i, failure in enumerate(failures[: cfg["recovery_attempts"]]):
            if not failure["selected_snapshot"]:
                summary["recovery"].append(
                    {
                        "task_id": failure["task_id"],
                        "attempted": False,
                        "reason": "no_active_legal_boundary",
                    }
                )
                continue
            directory = output / "recovery" / f"recovery_{i:02d}_{failure['task_id']}"
            record = teacher(
                failure["task_id"],
                directory,
                source=failure["selected_snapshot"]["snapshot_ref"],
                legacy=failure if failure.get("teacher_source_directory") else None,
            )
            if failure.get("teacher_source_directory"):
                from .v2_legacy_teacher import call

                acceptance = call(
                    python,
                    failure["teacher_source_directory"],
                    failure["teacher_source_lock_sha256"],
                    "suffix",
                    {"directory": str(directory), "identity": identity},
                )
            else:
                acceptance = prepare_recovery(directory, tokenizer)
            summary["recovery"].append(
                {
                    "task_id": failure["task_id"],
                    "attempted": True,
                    "official_success": record["reward"] == 1,
                    "acceptance": acceptance,
                    "directory": str(directory),
                    "teacher_source_directory": failure.get("teacher_source_directory"),
                }
            )
            if acceptance["status"] == "eligible":
                recovery_rows.extend(read_rows(directory / "examples.jsonl"))

        generated, goal_keys = [], set()
        for i, failure in enumerate(failures[: cfg["generator_proposals"]]):
            history = read_json(Path(failure["directory"]) / "history.json")
            feedback = {
                "task_id": failure["task_id"],
                "termination": failure["termination"],
                "public_history": history["messages"],
            }
            directory = output / "generation" / f"explore_{i:02d}_{failure['task_id']}"
            record = teacher(failure["task_id"], directory, exploration=feedback)
            try:
                task = candidate(record, feedback, directory)
                goal = digest(
                    {k: task[k] for k in ("source_task_id", "read", "categories")}
                )
                if goal in goal_keys:
                    raise ValueError("Duplicate generated goal")
                checks = []
                for mode in ("positive", "noop", "omit_completion", "wrong_answer"):
                    check_dir = directory / mode
                    payload = {
                        "task": task,
                        "mode": mode,
                        "appworld_root": deployment["appworld_root"],
                        "output": str(check_dir),
                    }
                    isolated(python, "generator-check", payload, check_dir, deadline)
                    checks.append(read_json(check_dir / "validation.json"))
                task = accept(task, checks)
                write_json(directory / "accepted_task.json", task)
                generated.append(task)
                goal_keys.add(goal)
                summary["generated"].append(
                    {
                        "task_id": task["task_id"],
                        "accepted": True,
                        "directory": str(directory),
                    }
                )
            except (ValueError, KeyError, TypeError) as exc:
                write_json(
                    directory / "rejected_proposal.json",
                    {"type": type(exc).__name__, "reason": str(exc)},
                )
                summary["generated"].append(
                    {"accepted": False, "reason": str(exc), "directory": str(directory)}
                )
        write_json(output / "generated_pool.json", generated)
        if not recovery_rows:
            raise RuntimeError("No qualified recovery suffix; S1 cannot be claimed")
        mixed = mix_recovery(recovery_rows, read_rows(Path(data) / "examples.jsonl"))
        write_rows(output / "recovery_mix.jsonl", mixed)
        s1 = output / "S1_recovery"
        train_sft(
            cfg,
            identity,
            output / "recovery_mix.jsonl",
            s1,
            parent=s0,
            deadline=deadline,
        )
        summary["real_checkpoints"]["S1_recovery"] = str(s1)
        schedule = []
        originals = plan()["feedback20"]
        for i in range(cfg["grpo"]["groups"]):
            if generated and i % 4 == 3:
                task = generated[(i // 4) % len(generated)]
                schedule.append(
                    {
                        "source": "generated",
                        "task_id": task["source_task_id"],
                        "generated_task": task,
                    }
                )
            else:
                schedule.append(
                    {"source": "original", "task_id": originals[i % len(originals)]}
                )
        write_json(output / "grpo_schedule.json", schedule)

        def rollout(model, checkpoint_id, task, group, member):
            return run_student(
                model,
                checkpoint_id,
                task["task_id"],
                output / "rollouts" / f"group_{group:02d}_member_{member}",
                "grpo",
                seed=20261109 + group * 4 + member,
                generated=task.get("generated_task"),
            )

        s2 = output / "S2_grpo"
        train_grpo(
            cfg,
            identity,
            s1,
            s2,
            tokenizer,
            schedule,
            rollout,
            deadline,
            remaining_tokens=lambda: ledger["limit"] - ledger["used"],
        )
        summary["real_checkpoints"]["S2_grpo"] = str(s2)
        updates = read_json(s2 / "updates.json")
        summary["grpo"] = {
            "actual_groups": sum(len(u["groups"]) for u in updates),
            "optimizer_steps": sum(bool(u["update"]["updated"]) for u in updates),
            "reward_varying_groups": sum(u["reward_varying_groups"] for u in updates),
            "generated_tasks_contributing_to_updates": sorted(
                {
                    t
                    for u in updates
                    for t in u["update"].get("contributing_generated_task_ids", [])
                }
            ),
        }
        for name, path in summary["real_checkpoints"].items():
            model = load_policy(path)
            results = []
            for task in plan()["dev24"]:
                results.append(
                    run_student(
                        model,
                        policy_id(model),
                        task,
                        output / "evaluation" / name / f"{name}_{task}",
                        "evaluation",
                    )
                )
            summary["evaluations"][name] = results
            del model
            gc.collect()
            torch.cuda.empty_cache()
        summary["status"] = (
            "completed"
            if summary["grpo"]["generated_tasks_contributing_to_updates"]
            else "completed_without_generated_task_learning_signal"
        )
    except Exception as exc:
        summary.update(status="stopped", error_type=type(exc).__name__, error=str(exc))
        (output / "failure.log").write_text(traceback.format_exc())
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        summary.update(
            training_generated_tokens=ledger["used"],
            wall_seconds=time.monotonic() - started,
        )
        write_json(output / "round_summary.json", summary)
    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "command",
        choices=[
            "prepare",
            "download-model",
            "run",
            "teacher-worker",
            "generator-check",
        ],
    )
    p.add_argument("--input")
    p.add_argument("--output")
    p.add_argument("--data")
    p.add_argument("--resume-base-from")
    p.add_argument("--carry-clock-from")
    args = p.parse_args()
    cfg = config()
    identity = cfg["models"][cfg["selected_model"]]
    if args.command == "prepare":
        print(json.dumps(prepare(args.output, identity)))
    elif args.command == "download-model":
        from huggingface_hub import snapshot_download

        if not __import__("torch").cuda.is_available():
            raise ValueError("Download full weights only on the authorized GPU host")
        print(
            snapshot_download(
                identity["model"],
                revision=identity["revision"],
                allow_patterns=["*.json", "*.jinja", "*.safetensors", "*.txt"],
            )
        )
    elif args.command == "run":
        print(
            json.dumps(
                run(
                    read_json(args.input),
                    args.output,
                    args.data,
                    resume_base_from=args.resume_base_from,
                    carry_clock_from=args.carry_clock_from,
                )
            )
        )
    elif args.command == "teacher-worker":
        from .p3a import run_teacher_episode
        from .teacher import read_credentials

        payload = read_json(args.input)
        run_teacher_episode(
            payload["config"],
            payload["task_id"],
            payload["output"],
            read_credentials(ROOT / ".env"),
            recovery_source=payload["source"],
            exploration=payload["exploration"],
        )
    elif args.command == "generator-check":
        from .adapter import AppWorldAdapter
        from .schema_compatibility import SCHEMA_REVISION
        from .v2_generator import replay
        from .v2_tasks import context_for

        payload = read_json(args.input)
        output = Path(payload["output"])
        output.mkdir(parents=True, exist_ok=False)
        task = payload["task"]
        adapter = AppWorldAdapter(
            payload["appworld_root"],
            task["source_task_id"],
            output.parent.name + "_" + output.name,
            max_interactions=sys.maxsize,
            research=context_for(task["source_task_id"], training=True),
        )
        try:
            adapter.configure_schema_compatibility(SCHEMA_REVISION, output)
            replay(adapter, task, payload["mode"], output)
        finally:
            adapter.close()


if __name__ == "__main__":
    main()
