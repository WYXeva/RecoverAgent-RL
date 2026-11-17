"""One real context and at most one admitted task x four S0 rollouts; never train."""

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import fcntl
from pathlib import Path
import time

from recoveragent.io import digest, read_json, write_json
from recoveragent.s2_experiment import configuration
from recoveragent.s2_state import evidence, protocol_identity, unknown_episode
from recoveragent.v2_runtime import S2_RUNTIME, verify_lock


def student_judge_budget(cfg):
    from recoveragent.coevolve_llm import call_identity

    return {
        **cfg["s2"]["teacher_budget"],
        "requests": 4,
        "generated_tokens": 4 * call_identity()["max_tokens"],
        "seconds": 4 * 120,
        "input_tokens": 4 * cfg["teacher"]["billing_context_tokens"],
        "priced_cost": 10,
    }


def generate(cfg, deployment, event_path, output):
    from recoveragent.s2_evolution import GroupEvolution
    from recoveragent.v2_round import isolated

    event = read_json(event_path)
    local = copy.deepcopy(cfg)
    local["task_evolution"].update(max_proposals=2, max_feedback_cycles=1)
    limits = local["s2"]["teacher_budget"]
    limits.update(
        requests=243,
        generated_tokens=3 * 16384 + 3 * 4096,
        input_tokens=243 * cfg["teacher"]["billing_context_tokens"],
        seconds=3 * 1200 + 3 * 120,
        priced_cost=100,
    )
    evidence(
        output / "generation_binding.json",
        {
            "event_sha256": digest(event),
            "config": local,
            "protocols": protocol_identity(),
            "source_lock": verify_lock(S2_RUNTIME),
            "scope": "one context, max two candidates; no Student or training",
        },
    )
    engine = GroupEvolution(
        local,
        cfg["models"][cfg["selected_model"]],
        {**deployment, "teacher_budget_amount": limits["priced_cost"]},
        output,
        {"teacher_usage_priced_usd": 0, "feedback": []},
        time.monotonic() + deployment["deadline_epoch"] - time.time(),
        isolated,
        budget_root=output,
    )
    return engine.run_context(event, output / "context_001")


def student(cfg, deployment, generation_path, output):
    from recoveragent.s2_backend import BackendRuntime
    from recoveragent.s2_schedule import GroupFeedback
    from recoveragent.v2_grpo import group_advantages
    from recoveragent.v2_s1_batch import checkpoint
    from recoveragent.coevolve_tasks import validate_task

    accepted = read_json(generation_path)["accepted"]
    if not accepted:
        result = {"status": "not_run_no_accepted_task", "training_updates": 0}
        evidence(output / "summary.json", result)
        return result
    # Deterministic first admitted candidate; never replace based on Student results.
    generated = accepted[0]
    validate_task(generated, generated["source_task_id"])
    task = {
        "task_id": generated["source_task_id"],
        "generated_task": generated,
    }
    actor = deployment["s0"]
    checkpoint(actor, cfg, initial=True)
    evidence(
        output / "binding.json",
        {
            "task": task,
            "actor": actor,
            "source_lock": verify_lock(S2_RUNTIME),
            "protocols": protocol_identity(),
            "attempts": 4,
            "seed": 20261117,
            "limits": cfg["student_limits"],
            "training_updates": 0,
        },
    )
    limits = student_judge_budget(cfg)
    runtime = BackendRuntime(cfg, {**deployment, "reward_judge_budget": limits}, output)
    try:
        if any(
            not (output / f"member_{i}.receipt.json").exists()
            and not (output / f"member_{i}.claim.json").exists()
            for i in range(4)
        ):
            runtime.prepare_rollouts(actor)
        policy = runtime.policy(actor)

        def member(i):
            directory = output / f"member_{i}"
            receipt = output / f"member_{i}.receipt.json"
            claim = output / f"member_{i}.claim.json"
            seed = 20261117 + i
            if receipt.exists():
                return read_json(receipt)
            if (directory / "result.json").exists():
                result = runtime.finalize_reward(
                    read_json(directory / "result.json"), directory
                )
                result.update(directory=str(directory), seed=seed)
            elif claim.exists():
                result = unknown_episode(
                    task,
                    policy,
                    directory,
                    "Interrupted diagnostic; no resampling",
                    seed,
                    cfg["student_limits"],
                )
            else:
                evidence(
                    claim, {"task_sha256": digest(task), "policy": policy, "seed": seed}
                )
                result = runtime.rollout(actor, task, directory, seed)
            write_json(receipt, result)
            return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            episodes = list(executor.map(member, range(4)))
        feedback = GroupFeedback(cfg["task_evolution"]["signals"]).observe(
            "probe_001", episodes
        )
        result = {
            "task_id": generated["task_id"],
            "instruction": generated["instruction"],
            "episodes": episodes,
            "feedback": feedback,
            "advantages_check_only": group_advantages(episodes)
            if feedback["complete"]
            else None,
            "training_updates": 0,
            "success": sum(r["success"] is True for r in episodes),
            "failure": sum(r["success"] is False for r in episodes),
            "invalid": sum(r["success"] is None for r in episodes),
        }
        evidence(output / "summary.json", result)
        return result
    finally:
        runtime.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["generate", "student"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cfg, deployment = configuration(), read_json(args.deployment)
    if not cfg or deployment.get("deadline_epoch", 0) <= time.time():
        raise ValueError("A current authorized deadline is required")
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = (generate if args.command == "generate" else student)(
            cfg, deployment, args.input, args.output.resolve()
        )
    print(
        {
            k: result[k]
            for k in [
                "candidate_count",
                "success",
                "failure",
                "invalid",
                "error",
                "training_updates",
            ]
            if k in result
        }
    )


if __name__ == "__main__":
    main()
