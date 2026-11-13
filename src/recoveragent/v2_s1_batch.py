"""Thin resumable stage orchestration; reuse existing episodes, recovery and SFT."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import random
import subprocess
import time

from .demonstrations import sha256
from .io import digest, read_json, write_json
from .v2_runtime import S1_RUNTIME, verify_lock
from .v2_tasks import ROOT, plan

CONFIG = ROOT / f"configs/{S1_RUNTIME}.json"


def configuration():
    cfg = read_json(CONFIG)
    verify_lock(S1_RUNTIME)
    selection = cfg["selection"]
    train = {r["task_id"] for r in plan()["rows"] if r["role"] == "train"}
    excluded = set(selection["excluded_task_ids"])
    available = sorted(train - excluded)
    if (
        len(excluded) != 256
        or not excluded <= train
        or len(available) != 254
        or available != selection["eligible_task_ids"]
        or random.Random(selection["seed"]).sample(available, 100)
        != cfg["feedback_task_ids"]
        or cfg["dev_task_ids"] != plan()["dev24"]
        or cfg["recovery_attempts"] is not None
        or cfg["generator_proposals"] != 30
        or cfg["task_evolution"]["max_proposals"] != 30
        or cfg["recovery_sft"]["recovery_supervised_token_fraction"] != 0.5
    ):
        raise ValueError("Frozen selection/stage contract changed")
    return cfg


def checkpoint(path, cfg, *, initial=False):
    from .v2_training import verify_checkpoint

    receipt = verify_checkpoint(path, cfg["models"][cfg["selected_model"]])
    if initial and (
        sha256(Path(path) / "checkpoint_receipt.json")
        != cfg["s0"]["checkpoint_receipt_sha256"]
        or receipt["adapter_sha256_after"] != cfg["s0"]["adapter_sha256"]
        or receipt["optimizer_steps"] != cfg["s0"]["optimizer_steps"]
    ):
        raise ValueError("This is not the accepted S0")
    return receipt


def initialize(output, cfg, deployment):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("run_config.json", cfg), ("deployment.json", deployment)):
        target = output / name
        if target.exists():
            if read_json(target) != value:
                raise ValueError("Existing batch configuration changed")
        else:
            write_json(target, value)


def progress(output, name, value):
    """Append immutable evidence; atomically refresh a derived latest view."""
    directory = Path(output) / "progress" / name
    directory.mkdir(parents=True, exist_ok=True)
    record = directory / f"{len(list(directory.glob('*.json')))+1:05d}.json"
    write_json(record, value)
    target = Path(output) / (name + "_summary.json")
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(record.read_bytes())
    os.replace(temporary, target)


def carry_attempts(cfg, deployment, output, policy):
    """Carry the two resolved-response, unscored infrastructure attempts once."""
    source = Path(deployment["feedback_source"])
    expected = cfg["carry_from"]
    old = read_json(source / "run_config.json")
    if sha256(source / "run_config.json") != expected["config_sha256"]:
        # Config files have the same JSON value but use different serialization.
        if old != read_json(ROOT / ("configs/" + expected["runtime"] + ".json")):
            raise ValueError("Feedback source configuration changed")
    original = dict(cfg)
    original.pop("carry_from")
    for key in ("format", "execution_protocol", "source_lock"):
        original[key] = old[key]
    if original != old or sha256(ROOT / old["source_lock"]) != expected["lock_sha256"]:
        raise ValueError("Feedback continuation changed the frozen experiment")
    actual_dirs = {p.name for p in (source / "feedback").iterdir() if p.is_dir()}
    if actual_dirs != set(expected["attempted_task_ids"]):
        raise ValueError("Unexpected source attempt set; no implicit replay")
    results = {}
    for task in expected["attempted_task_ids"]:
        directory = source / "feedback" / task
        qcfg = read_json(directory.with_name(task + ".config.json"))
        if any(
            qcfg[k] != v
            for k, v in {
                "model": cfg["models"][cfg["selected_model"]],
                "task_id": task,
                "checkpoint_id": policy["checkpoint_id"],
                "student_limits": cfg["student_limits"],
                "execution_protocol": expected["runtime"],
                "purpose": "feedback",
            }.items()
        ):
            raise ValueError("Carried feedback model or budgets changed")
        if (directory / "result.json").exists():
            raise ValueError(
                "Expected the explicitly recorded missing-final-receipt failure"
            )
        requests = sorted((directory / "session/requests").glob("*.request.json"))
        if not requests:
            raise ValueError("No real generation in source attempt")
        evidence, inputs, generated, seconds = {}, 0, 0, 0.0
        for path in requests:
            response = path.with_name(
                path.name.replace(".request.json", ".response.json")
            )
            q, a = read_json(path), read_json(response)
            if (
                q["policy"] != policy
                or a["policy"] != policy
                or q["input_token_ids"] != a["input_token_ids"]
            ):
                raise ValueError("Carried behavior receipt changed")
            evidence[str(path)] = sha256(path)
            evidence[str(response)] = sha256(response)
            inputs += len(q["input_token_ids"])
            generated += len(a["token_ids"])
            seconds += a["generation_seconds"]
        log = directory.with_name(task + ".worker.log")
        if "NameError: name 'torch' is not defined" not in log.read_text():
            raise ValueError("Unexpected source infrastructure failure")
        evidence[str(log)] = sha256(log)
        results[task] = {
            "task_id": task,
            "policy": policy,
            "directory": str(directory),
            "purpose": "feedback",
            "classification": "infrastructure_error",
            "success": None,
            "evaluation": None,
            "selected_snapshot": None,
            "usage": {
                "model_requests": len(requests),
                "input_tokens": inputs,
                "generated_tokens": generated,
                "unknown_generation_reservation": 0,
            },
            "costs": {"model_seconds": seconds},
            "fault": {
                "category": "worker_environment",
                "reason": expected["reason"],
                "worker_log": str(log),
            },
            "source_evidence": evidence,
            "automatic_replay": False,
        }
    proof = output / "carried_feedback.json"
    if proof.exists():
        if read_json(proof) != results:
            raise ValueError("Carried source evidence changed")
    else:
        write_json(proof, results)
    return results


def summarize(results, total):
    counts = {
        key: sum(r["classification"] == key for r in results)
        for key in ("success", "natural_failure", "infrastructure_error")
    }
    return {
        "attempted": len(results),
        "planned": total,
        **counts,
        "success_rate_completed": counts["success"] / len(results) if results else None,
        "success_rate_full": counts["success"] / total
        if len(results) == total
        else None,
        "results": results,
    }


def feedback(cfg, deployment, output, *, evaluation=False):
    from .v2_inference import episode
    from .v2_model import load_tokenizer
    from .v2_vllm import Backend

    identity = cfg["models"][cfg["selected_model"]]
    model_path = deployment["s1"] if evaluation else deployment["s0"]
    saved = checkpoint(model_path, cfg, initial=not evaluation)
    policy = {
        **identity,
        "checkpoint_id": digest({"adapter_sha256": saved["adapter_sha256_after"]}),
    }
    tasks = cfg["dev_task_ids"] if evaluation else cfg["feedback_task_ids"]
    stage = "evaluation_S1" if evaluation else "feedback"
    destination = output / stage
    destination.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0, deployment["job_deadline_epoch"] - time.time())
    model = None
    if evaluation:
        from peft import PeftModel
        from .v2_model import load_base

        model = PeftModel.from_pretrained(
            load_base(identity), model_path, is_trainable=False
        )
        model.eval()
    finished = {} if evaluation else carry_attempts(cfg, deployment, output, policy)
    for task in tasks:
        directory = destination / task
        if directory.exists():
            result = read_json(directory / "result.json")
            if (
                result["policy"] != policy
                or result["task_id"] != task
                or result["purpose"] != ("evaluation" if evaluation else "feedback")
                or result["classification"] == "infrastructure_error"
            ):
                raise ValueError(
                    "Unresolved prior attempt or policy mismatch; no task replay"
                )
            finished[task] = {**result, "directory": str(directory)}

    def dispatch(task):
        if time.monotonic() + cfg["student_limits"]["wall_seconds"] + 120 > deadline:
            raise TimeoutError("Insufficient remaining budget for a complete task")
        directory = destination / task
        request = {
            "model": identity,
            "checkpoint_id": policy["checkpoint_id"],
            "student_limits": cfg["student_limits"],
            "task_id": task,
            "appworld_root": deployment["appworld_root"],
            "output": str(directory),
            "purpose": "evaluation" if evaluation else "feedback",
            "environment_contract": cfg["environment_contract"],
            "execution_protocol": S1_RUNTIME,
        }
        tokenizer = load_tokenizer(identity)
        backend = (
            None
            if evaluation
            else Backend(
                deployment["vllm_url"], deployment["vllm_receipt"], policy, "S0_base"
            )
        )
        result = episode(
            request,
            model,
            tokenizer,
            python=deployment["worker_python"],
            deadline=deadline,
            generate_fn=backend,
        )
        return {**result, "directory": str(directory)}

    # Only a small fixed batch is in flight. Failure prevents subsequent batches.
    pending = [t for t in tasks if t not in finished]
    concurrency = 1 if evaluation else cfg["inference"]["max_num_seqs"]
    for start in range(0, len(pending), concurrency):
        errors = []
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(dispatch, t): t
                for t in pending[start : start + concurrency]
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                    finished[task] = result
                    if result["classification"] == "infrastructure_error":
                        errors.append(task + ":infrastructure_error")
                except Exception as exc:
                    errors.append(task + ":" + type(exc).__name__ + ":" + str(exc))
                    result_path = destination / task / "result.json"
                    if result_path.exists():
                        result = read_json(result_path)
                        if (
                            result.get("policy") == policy
                            and result.get("task_id") == task
                        ):
                            finished[task] = {
                                **result,
                                "directory": str(result_path.parent),
                            }
                ordered = [finished[t] for t in tasks if t in finished]
                summary = summarize(ordered, len(tasks))
                summary.update(
                    errors=errors, remaining=[t for t in tasks if t not in finished]
                )
                progress(output, stage, summary)
                print(
                    json.dumps(
                        {
                            k: v
                            for k, v in summary.items()
                            if k not in {"results", "remaining"}
                        }
                    ),
                    flush=True,
                )
        if errors:
            raise RuntimeError(";".join(errors))
    return read_json(output / (stage + "_summary.json"))


def isolated(python, command, payload, directory, deadline):
    from .v2_round import isolated as execute

    return execute(python, command, payload, directory, deadline)


def recover(cfg, deployment, output):
    from .v2_teacher import configuration as teacher_config
    from .v2_model import load_tokenizer
    from .v2_data import prepare_recovery, read_rows, write_rows

    feedback_summary = read_json(output / "feedback_summary.json")
    if feedback_summary["attempted"] != 100:
        raise ValueError("Complete resolved feedback is required")
    identity = cfg["models"][cfg["selected_model"]]
    tokenizer = load_tokenizer(identity)
    entries, rows = [], []
    deadline = time.monotonic() + max(0, deployment["job_deadline_epoch"] - time.time())
    failures = [
        r
        for r in feedback_summary["results"]
        if r["classification"] == "natural_failure"
    ]
    for failure in failures:
        task, source = failure["task_id"], failure["selected_snapshot"]
        if source is None:
            entries.append(
                {
                    "task_id": task,
                    "attempted": False,
                    "reason": "no_active_legal_boundary",
                }
            )
        else:
            directory = output / "recovery" / task
            launch = directory.parent / (task + ".input.json")
            if not directory.exists() and not launch.exists():
                if (
                    time.monotonic() + cfg["student_limits"]["wall_seconds"] + 100
                    > deadline
                ):
                    break
                cfg_teacher = teacher_config(
                    task, identity, deployment["appworld_root"], 1e9, runtime=S1_RUNTIME
                )
                payload = {
                    "config": cfg_teacher,
                    "task_id": task,
                    "output": str(directory),
                    "source": source["snapshot_ref"],
                    "exploration": None,
                }
                try:
                    isolated(
                        deployment["worker_python"],
                        "teacher-worker",
                        payload,
                        directory,
                        deadline,
                    )
                except (subprocess.SubprocessError, OSError) as exc:
                    write_json(
                        directory / "controller_error.json",
                        {"error_type": type(exc).__name__, "automatic_retry": False},
                    )
            trajectory = directory / "trajectory.json"
            if not trajectory.exists():
                entries.append(
                    {
                        "task_id": task,
                        "attempted": True,
                        "status": "infrastructure_error",
                        "acceptance": {
                            "status": "rejected",
                            "reasons": ["missing_worker_receipt"],
                        },
                    }
                )
            else:
                record = read_json(trajectory)
                if record["task_id"] != task:
                    raise ValueError("Recovery task/source binding changed")
                acceptance_path = directory / "suffix_acceptance.json"
                if record["status"] != "valid":
                    acceptance = {
                        "status": "rejected",
                        "reasons": ["infrastructure_error"],
                    }
                else:
                    if (
                        Path(record["recovery"]["snapshot_ref"]).resolve()
                        != Path(source["snapshot_ref"]).resolve()
                    ):
                        raise ValueError(
                            "Recovery did not continue the selected Student checkpoint"
                        )
                    acceptance = (
                        read_json(acceptance_path)
                        if acceptance_path.exists()
                        else prepare_recovery(directory, tokenizer, max_length=65536)
                    )
                entries.append(
                    {
                        "task_id": task,
                        "attempted": True,
                        "reward": record["reward"],
                        "status": record["status"],
                        "usage": record["usage"],
                        "acceptance": acceptance,
                    }
                )
                if acceptance["status"] == "eligible":
                    rows.extend(read_rows(directory / "examples.jsonl"))
        summary = {
            "entries": entries,
            "planned_failures": len(failures),
            "processed_failures": len(entries),
            "eligible": sum(
                e.get("acceptance", {}).get("status") == "eligible" for e in entries
            ),
            "deferred": sum(
                e.get("acceptance", {}).get("status") == "deferred" for e in entries
            ),
            "official_successes": sum(
                e.get("status") == "valid" and e.get("reward") == 1 for e in entries
            ),
            "infrastructure_errors": sum(
                e.get("attempted", False) and e.get("status") != "valid"
                for e in entries
            ),
            "attempts": sum(e["attempted"] for e in entries),
            "supervised_tokens": sum(sum(r["loss_mask"]) for r in rows),
        }
        progress(output, "recovery", summary)
        print(
            json.dumps({k: v for k, v in summary.items() if k != "entries"}), flush=True
        )
    if len(entries) != len(failures):
        raise TimeoutError("Recovery budget ended; partial real attempts preserved")
    target = output / "data/recovery_examples.jsonl"
    if target.exists():
        if read_rows(target) != rows:
            raise ValueError("Existing recovery export differs from verified attempts")
    else:
        write_rows(target, rows)


def evolve(cfg, deployment, output):
    from .coevolve_generation import Evolution

    results = read_json(output / "feedback_summary.json")["results"]
    if len(results) != 100:
        raise ValueError("All frozen train attempts must be accounted for")
    results = [r for r in results if r["classification"] != "infrastructure_error"]
    target = output / "coevolve"
    target.mkdir(parents=True, exist_ok=False)
    summary = {"feedback": results, "teacher_usage_priced_usd": 0.0}
    deadline = time.monotonic() + max(0, deployment["job_deadline_epoch"] - time.time())
    evolution = Evolution(
        cfg,
        cfg["models"][cfg["selected_model"]],
        {**deployment, "teacher_budget_amount": 1e9},
        target,
        summary,
        deadline,
        isolated,
    )
    summary["status"] = "running"
    try:
        evolution.initial(results)
        while (
            len(evolution.attempts) < 30
            and evolution.cycle < cfg["task_evolution"]["max_feedback_cycles"]
        ):
            before = evolution.cycle
            evolution.evolve("train100")
            if evolution.cycle == before:
                break
        summary["status"] = "complete"
    except Exception as exc:
        summary.update(status="interrupted", error=type(exc).__name__ + ":" + str(exc))
        raise
    finally:
        evolution.snapshot("final_pool.json")
        write_json(output / "coevolve_summary.json", summary)


def prepare_mix(cfg, deployment, output):
    from .v2_data import read_rows, mix_recovery, write_rows
    from .v2_model import load_tokenizer
    from scripts.train_v2_base_64k import prepare, CONFIG as BASE_CONFIG

    base = output / "data/normal"
    if not (base / "preparation.json").exists():
        prepare(
            deployment["dataset"],
            base,
            read_json(BASE_CONFIG),
            load_tokenizer(cfg["models"][cfg["selected_model"]]),
        )
    recovery = read_rows(output / "data/recovery_examples.jsonl")
    if not recovery:
        raise ValueError("No eligible recovery suffixes; cannot claim S1")
    rows = mix_recovery(
        recovery,
        read_rows(base / "examples.jsonl"),
        recovery_fraction=cfg["recovery_sft"]["recovery_supervised_token_fraction"],
    )
    target = output / "data/s1_examples.jsonl"
    if target.exists():
        if read_rows(target) != rows:
            raise ValueError("Existing S1 examples differ from verified source data")
    else:
        write_rows(target, rows)
    write_json(
        output / "data/mix.json",
        {
            "examples": len(rows),
            "sha256": sha256(target),
            "recovery_fraction": cfg["recovery_sft"][
                "recovery_supervised_token_fraction"
            ],
            "kinds": {
                k: {
                    "examples": sum(r["mix_kind"] == k for r in rows),
                    "tasks": len({r["task_id"] for r in rows if r["mix_kind"] == k}),
                    "supervised_tokens": sum(
                        sum(r["loss_mask"]) for r in rows if r["mix_kind"] == k
                    ),
                }
                for k in ("recovery", "normal")
            },
        },
    )


def train(cfg, deployment, output):
    identity = cfg["models"][cfg["selected_model"]]
    parent = checkpoint(deployment["s0"], cfg, initial=True)
    for profile, name in (
        ("short", "smoke_short"),
        ("long", "smoke_long"),
        ("formal", "S1_recovery"),
    ):
        target = output / name
        if not target.exists():
            payload = {
                "config": cfg,
                "identity": identity,
                "examples": str(output / "data/s1_examples.jsonl"),
                "output": str(target),
                "parent": deployment["s0"],
                "profile": profile,
                "deadline_epoch": deployment["job_deadline_epoch"],
            }
            request = output / (name + ".input.json")
            write_json(request, payload)
            with (output / (name + ".log")).open("x") as log:
                subprocess.run(
                    [
                        deployment["gpu_python"],
                        str(ROOT / "scripts/run_v2_s1.py"),
                        "train-worker",
                        "--input",
                        str(request),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
        receipt = checkpoint(target, cfg)
        if (
            receipt["adapter_sha256_before"] != parent["adapter_sha256_after"]
            or receipt["profile"] != profile
        ):
            raise ValueError("S1 lineage/profile changed")
        accepted = output / (name + "_accepted.json")
        if accepted.exists():
            if read_json(accepted) != receipt:
                raise ValueError("Checkpoint acceptance changed")
        else:
            write_json(accepted, receipt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage",
        choices=[
            "feedback",
            "recovery",
            "coevolve",
            "prepare-mix",
            "train",
            "evaluation",
            "train-worker",
        ],
    )
    parser.add_argument("--deployment")
    parser.add_argument("--output")
    parser.add_argument("--input")
    args = parser.parse_args()
    cfg = configuration()
    if args.stage == "train-worker":
        from .v2_training import train_sft

        payload = read_json(args.input)
        if payload.pop("config") != cfg:
            raise ValueError("Training config mismatch")
        payload["deadline"] = time.monotonic() + max(
            0, payload.pop("deadline_epoch") - time.time()
        )
        train_sft(cfg, **payload)
        return
    deployment = read_json(args.deployment)
    output = Path(args.output).resolve()
    initialize(output, cfg, deployment)
    if args.stage in {"feedback", "evaluation"}:
        feedback(cfg, deployment, output, evaluation=args.stage == "evaluation")
    else:
        {
            "recovery": recover,
            "coevolve": evolve,
            "prepare-mix": prepare_mix,
            "train": train,
        }[args.stage](cfg, deployment, output)


if __name__ == "__main__":
    main()
