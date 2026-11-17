"""Exactly four frozen train tasks x four S0 attempts. No training or generation."""

import argparse
import os
from pathlib import Path

from .io import digest, read_json, write_json
from .s2_state import evidence, unknown_episode, protocol_identity
from .v2_runtime import S2_GOAL_REPAIR_EXECUTION, S2_RUNTIME, verify_lock


def prepare(plan_path, output):
    from .coevolve_tasks import validate_task
    from .v2_tasks import context_for

    plan = read_json(plan_path)
    root = Path(__file__).resolve().parents[2]
    recipe = read_json(root / "configs/s2_reward_fresh16_repair_20261117.json")
    if digest(plan) != recipe["plan_sha256"]:
        raise ValueError("Fresh16 task definitions differ from the reviewed manifest")
    if len(plan["tasks"]) != 4 or [t["coverage"] for t in plan["tasks"]] != [
        "scalar_number",
        "cart_json",
        "product_question",
        "quote_email",
    ]:
        raise ValueError("Probe must freeze the four requested task categories")
    metadata_proofs = {}
    for task in plan["tasks"]:
        context_for(task["task_id"], training=True)
        if task.get("generated_task"):
            validate_task(
                task["generated_task"], task["task_id"], require_validation=False
            )
            from .coevolve_goal import tables
            from .coevolve_goal_schema import related_metadata, validate_fields

            spec = task["generated_task"]["success_conditions"]
            apps = sorted({table.split(".")[0] for table in tables(spec)})
            metadata = related_metadata(
                [{"action": {"name": app + "__metadata"}} for app in apps]
            )
            validate_fields(spec, metadata)
            metadata_proofs[task["coverage"]] = digest(metadata)
    cfg = read_json(root / f"configs/{S2_RUNTIME}.json")
    frozen = {
        "revision": S2_GOAL_REPAIR_EXECUTION,
        "protocols": protocol_identity(),
        "model_metadata_sha256": metadata_proofs,
        "plan_sha256": digest(plan),
        "plan": plan,
        "s0": cfg["s0"],
        "attempts_per_task": 4,
        "total_attempts": 16,
        "seed": 20261117,
        "training": False,
        "teacher_generation": False,
        "admission": "diagnostic only; no automatic training pool import",
        "source_lock": verify_lock(S2_RUNTIME),
    }
    evidence(Path(output) / "frozen.json", frozen)
    return frozen


def run(output, deployment_path):
    from .s2_backend import BackendRuntime
    from .s2_experiment import configuration
    from .v2_training import verify_checkpoint

    output = Path(output)
    frozen = read_json(output / "frozen.json")
    if frozen.get("protocols") != protocol_identity():
        raise ValueError("Frozen probe protocol changed")
    if frozen["source_lock"] != verify_lock(S2_RUNTIME) or frozen[
        "plan_sha256"
    ] != digest(frozen["plan"]):
        raise ValueError("Frozen probe changed")
    cfg, deployment = configuration(), read_json(deployment_path)
    actor = deployment.get("s0_checkpoint") or deployment["s0"]
    receipt = verify_checkpoint(actor, cfg["models"][cfg["selected_model"]])
    if receipt["adapter_sha256_after"] != cfg["s0"]["adapter_sha256"]:
        raise ValueError("Fresh16 requires the accepted S0, not a later actor")
    runtime = BackendRuntime(cfg, deployment, output)
    results = []
    try:
        for i, item in enumerate(frozen["plan"]["tasks"]):
            group = []
            for member in range(4):
                directory = output / f"task_{i}" / f"member_{member}"
                claim = directory.with_suffix(".claim.json")
                ledger = directory.with_suffix(".receipt.json")
                seed = frozen["seed"] + i * 4 + member
                task = {**item, "diagnostic_goal_probe": True}
                if ledger.exists():
                    result = read_json(ledger)
                elif (directory / "result.json").exists():
                    result = runtime.finalize_reward(
                        read_json(directory / "result.json"), directory
                    )
                    result.update(directory=str(directory), seed=seed)
                    write_json(ledger, result)
                elif claim.exists():
                    result = unknown_episode(
                        task,
                        runtime.policy(actor),
                        directory,
                        "Interrupted probe slot retained; no resampling",
                        seed,
                        cfg["student_limits"],
                    )
                    write_json(ledger, result)
                else:
                    runtime.available(cfg["student_limits"]["wall_seconds"] + 120)
                    write_json(
                        claim,
                        {
                            "task_sha256": digest(task),
                            "policy": runtime.policy(actor),
                            "seed": seed,
                        },
                    )
                    result = runtime.rollout(actor, task, directory, seed)
                    write_json(ledger, result)
                group.append(result)
            known = [r for r in group if r.get("initial_state_hash")]
            if len({digest((r["policy"], r["initial_state_hash"])) for r in known}) > 1:
                raise ValueError("Probe group policy or clean initial state differs")
            results.extend(group)
        summary = {
            "attempts": len(results),
            "success": sum(r["success"] is True for r in results),
            "failure": sum(r["success"] is False for r in results),
            "invalid": sum(r["success"] is None for r in results),
            "results": results,
            "training_updates": 0,
            "teacher_generation": 0,
            "model_requests": sum(
                r.get("usage", {}).get("model_requests", 0) for r in results
            ),
            "generated_tokens": sum(
                r.get("usage", {}).get("generated_tokens", 0) for r in results
            ),
            "episode_seconds": sum(r.get("wall_seconds") or 0 for r in results),
            "semantic_judge_usage": [
                r.get("reward_judge_usage")
                for r in results
                if r.get("reward_judge_usage")
            ],
        }
        evidence(output / "summary.json", summary)
        return summary
    finally:
        runtime.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run", "resume"])
    parser.add_argument("--plan")
    parser.add_argument("--deployment")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    os.environ["RECOVERAGENT_S2_EXECUTION"] = S2_GOAL_REPAIR_EXECUTION
    if args.command == "prepare":
        if not args.plan:
            parser.error("prepare requires --plan (private frozen definitions)")
        result = prepare(args.plan, args.output)
        print(
            f"Prepared {result['total_attempts']} slots; no model/environment execution"
        )
    else:
        if not args.deployment:
            parser.error(
                "run/resume requires an explicitly authorized deployment/deadline"
            )
        result = run(args.output, args.deployment)
        print({k: result[k] for k in ("attempts", "success", "failure", "invalid")})
