#!/usr/bin/env python3
"""Finalize the last completed GRPO update after OOM, then run frozen dev24.

This does not resume Adam, replay rollouts, or discard the interrupted run.
Run against the original immutable execution checkout via PYTHONPATH.
"""

import argparse
import gc
import math
from pathlib import Path
import signal
import sys
import time
import traceback

from recoveragent.demonstrations import sha256
from recoveragent.io import digest, files_manifest, read_json, write_json


def completed_updates(source):
    source = Path(source)
    summary = read_json(source / "round_summary.json")
    if summary.get("error_type") != "OutOfMemoryError":
        raise ValueError("This entry only finalizes an explicitly stopped OOM run")
    rows = [read_json(p) for p in sorted((source / "S2_grpo/updates").glob("*.json"))]
    steps = 0
    for index, row in enumerate(rows):
        if row["batch"] != index:
            raise ValueError("Noncontiguous completed update receipts")
        update = row["update"]
        if update["updated"]:
            if (
                update["gradient_tensors"] <= 0
                or update["l2_norm"] <= 0
                or not math.isfinite(update["l2_norm"])
                or not math.isfinite(update["loss"])
            ):
                raise ValueError("Invalid completed gradient/loss receipt")
            steps += 1
    expected = {f"update_{i:02d}" for i in range(1, steps + 1)}
    actual = {p.name for p in (source / "S2_grpo").glob("update_*") if p.is_dir()}
    if not steps or actual != expected:
        raise ValueError("Missing, ambiguous, or unsaved completed update")
    checkpoint = source / "S2_grpo" / f"update_{steps:02d}"
    if not (checkpoint / "adapter_model.safetensors").is_file():
        raise ValueError("Last completed adapter is absent")
    # The interrupted batch was sampled with the last saved policy. It must
    # have no completed update receipt; its costs and trajectories stay intact.
    pending = []
    for group in range(2 * len(rows), 2 * len(rows) + 2):
        for member in range(4):
            path = (
                source / "rollouts" / f"group_{group:02d}_member_{member}/result.json"
            )
            if not path.is_file():
                raise ValueError("Interrupted batch lacks complete rollout receipts")
            pending.append(read_json(path))
    policies = {digest(r["policy"]) for r in pending}
    if len(policies) != 1:
        raise ValueError("Interrupted batch sampling policies differ")
    return rows, steps, checkpoint, pending


def run(payload, entry_config):
    import torch
    from peft import PeftModel, get_peft_model_state_dict
    from recoveragent.sft_recipe import tensor_hash
    from recoveragent.v2_grpo import segments
    from recoveragent.v2_inference import episode, policy_id
    from recoveragent.v2_model import load_base, load_tokenizer
    from recoveragent.v2_round import config
    from recoveragent.v2_runtime import verify_lock
    from recoveragent.v2_tasks import development_tasks, plan
    from recoveragent.v2_training import (
        finalize_reload,
        finish_checkpoint,
        verify_checkpoint,
    )

    entry_root = Path(__file__).resolve().parents[1]
    entry = read_json(entry_config)
    lock_path = entry_root / entry["source_lock"]
    entry_lock = read_json(lock_path)
    if any(
        sha256(entry_root / name) != value
        for name, value in entry_lock["files"].items()
    ):
        raise ValueError("Finalization entry source changed")
    locked = verify_lock()
    if locked["lock_sha256"] != entry["execution_lock_sha256"]:
        raise ValueError("Finalization must use the original locked runtime")
    cfg = config()
    source = Path(payload["source_run"])
    deployment = read_json(payload["deployment"])
    old_preflight = read_json(source / "preflight.json")
    if (
        read_json(source / "run_config.json") != cfg
        or old_preflight["source"] != locked
        or old_preflight["deployment"] != deployment
        or cfg["dev_tasks"] != 24
    ):
        raise ValueError("Original configuration, deployment or source mismatch")
    remaining = deployment["job_deadline_epoch"] - time.time()
    if remaining <= 0:
        raise TimeoutError("Original absolute deadline expired")
    deadline = time.monotonic() + remaining
    rows, steps, checkpoint, pending = completed_updates(source)
    identity = cfg["models"][cfg["selected_model"]]
    s0, s1 = Path(payload["s0_checkpoint"]), Path(payload["s1_checkpoint"])
    s0_receipt, s1_receipt = (
        verify_checkpoint(s0, identity),
        verify_checkpoint(s1, identity),
    )
    if (
        s1_receipt["parent_receipt_sha256"] != sha256(s0 / "checkpoint_receipt.json")
        or s1_receipt["adapter_sha256_before"] != s0_receipt["adapter_sha256_after"]
    ):
        raise ValueError("S0/S1 lineage mismatch")
    original_summary = read_json(source / "round_summary.json")
    if original_summary["real_checkpoints"] != {
        "S0_base": str(s0),
        "S1_recovery": str(s1),
    }:
        raise ValueError("Requested parents differ from interrupted run")
    output = Path(payload["output"])
    output.mkdir(parents=True, exist_ok=False)
    source_files = files_manifest(checkpoint)
    summary = {
        "status": "started",
        "original_run": str(source),
        "original_summary_sha256": sha256(source / "round_summary.json"),
        "original_failure": original_summary["error"],
        "execution_source": locked,
        "entry_lock_sha256": sha256(lock_path),
        "job_deadline_epoch": deployment["job_deadline_epoch"],
        "real_checkpoints": {"S0_base": str(s0), "S1_recovery": str(s1)},
        "evaluations": {},
        "new_training_or_teacher_requests": 0,
        "optimizer_steps": steps,
        "completed_update_batches": len(rows),
        "sampled_groups": sum(len(x["groups"]) for x in rows) + 2,
        "unapplied_oom_groups": 2,
        "optimizer_state_available": False,
        "selection_rule": "last_completed_update_before_OOM_no_dev_selection",
    }
    write_json(output / "input.json", payload)
    write_json(output / "run_config.json", cfg)
    write_json(
        output / "dev_selection.json",
        {
            "task_ids": development_tasks(24),
            "split_manifest_sha256": plan()["manifest_sha256"],
        },
    )
    write_json(output / "source_checkpoint_manifest.json", source_files)
    started = time.monotonic()

    def alarm(*_):
        raise TimeoutError("Original absolute GPU deadline reached")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        tokenizer = load_tokenizer(identity)
        model = PeftModel.from_pretrained(
            load_base(identity), str(checkpoint), is_trainable=False
        )
        expected_policy = {**identity, "checkpoint_id": policy_id(model)}
        if any(r["policy"] != expected_policy for r in pending):
            raise ValueError("Saved adapter does not match interrupted sampling policy")
        before = s1_receipt["adapter_sha256_after"]
        source_tensor_hash = tensor_hash(get_peft_model_state_dict(model))
        first = rows[0]["groups"][0][0]
        probe_ids = segments(first["directory"], first["policy"])[0]["prompt_ids"][:128]
        s2 = output / "S2_grpo"
        metadata = {
            "stage": "S2_grpo",
            "optimizer_steps": steps,
            "groups": sum(len(x["groups"]) for x in rows),
            "sampled_groups": summary["sampled_groups"],
            "scheduled_groups": cfg["grpo"]["groups"],
            "interrupted": True,
            "stop_reason": "OutOfMemoryError_before_third_optimizer_step",
            "source_intermediate_checkpoint": str(checkpoint),
            "source_adapter_sha256": source_tensor_hash,
            "source_checkpoint_files": source_files,
            "optimizer_state_available": False,
            "reference_adapter_sha256_at_initialization": before,
            "reference_final_hash_available": False,
            "beta": cfg["grpo"]["beta"],
            "training_wall_seconds_including_rollouts_and_evolution": original_summary[
                "wall_seconds"
            ],
            "gpu_peak_allocated_bytes": None,
            "gpu_peak_reserved_bytes": None,
        }
        after, probe = finish_checkpoint(
            model,
            tokenizer,
            s2,
            identity,
            before,
            parent=s1,
            metadata=metadata,
            probe_ids=probe_ids,
        )
        if after != source_tensor_hash or files_manifest(checkpoint) != source_files:
            raise ValueError("Finalization changed the saved update")
        del model
        gc.collect()
        torch.cuda.empty_cache()
        finalize_reload(s2, identity, before, after, probe, probe_ids, s1, metadata)
        summary["real_checkpoints"]["S2_grpo"] = str(s2)
        write_json(output / "progress/000.json", summary)
        for name, path in summary["real_checkpoints"].items():
            verify_checkpoint(path, identity)
            model = PeftModel.from_pretrained(
                load_base(identity), str(path), is_trainable=False
            )
            checkpoint_id = policy_id(model)
            results = []
            summary["evaluations"][name] = results
            for task in development_tasks(24):
                directory = output / "evaluation" / name / f"{name}_{task}"
                request = {
                    "task_id": task,
                    "model": identity,
                    "checkpoint_id": checkpoint_id,
                    "purpose": "evaluation",
                    "output": str(directory),
                    "appworld_root": deployment["appworld_root"],
                    "student_limits": {**cfg["student_limits"], "seed": 42},
                }
                result = episode(
                    request,
                    model,
                    tokenizer,
                    python=deployment.get("worker_python", sys.executable),
                    deadline=deadline,
                )
                results.append({**result, "directory": str(directory)})
                count = sum(len(v) for v in summary["evaluations"].values())
                write_json(output / "progress" / f"{count:03d}.json", summary)
                print(
                    {
                        "stage": name,
                        "completed": len(results),
                        "success": sum(r["success"] is True for r in results),
                    },
                    flush=True,
                )
            del model
            gc.collect()
            torch.cuda.empty_cache()
        summary["status"] = "evaluated_three_stages_after_interrupted_grpo"
    except Exception as exc:
        summary.update(status="stopped", error_type=type(exc).__name__, error=str(exc))
        (output / "failure.log").write_text(traceback.format_exc())
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        summary["wall_seconds"] = time.monotonic() - started
        write_json(output / "round_summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    run(read_json(args.input), args.config)
