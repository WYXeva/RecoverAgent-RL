"""One original train task x four attempts, then independent no-step GRPO probes.

prepare is CPU/read-only except its new output. sample/compare require a newly
explicit deadline and an available authorized GPU; neither starts formal S2.
"""

import argparse
import os
from pathlib import Path
import subprocess
import time

from recoveragent.demonstrations import sha256
from recoveragent.io import read_json, write_json
from recoveragent.s2_state import evidence, protocol_identity, atomic
from recoveragent.v2_runtime import S2_APPEND_EXECUTION, S2_APPEND_DOCS_EXECUTION

PROBES = {
    S2_APPEND_EXECUTION: "configs/s2_append_probe_20261118.json",
    S2_APPEND_DOCS_EXECUTION: "configs/s2_append_docs_probe_20261118.json",
}


def frozen(root):
    from recoveragent.s2_experiment import configuration
    from recoveragent.v2_training import verify_checkpoint

    plan = read_json(root / "plan.json")
    cfg = configuration()
    identity = cfg["models"][cfg["selected_model"]]
    actor = verify_checkpoint(plan["actor"], identity)
    if (
        cfg != plan["config"]
        or protocol_identity() != plan["protocols"]
        or actor["adapter_sha256_after"] != plan["actor_sha256"]
        or sha256(Path(plan["optimizer"])) != plan["optimizer_sha256"]
    ):
        raise ValueError("Frozen probe protocol/actor/optimizer changed")
    return plan


def prepare(args):
    from recoveragent.s2_experiment import configuration
    from recoveragent.v2_training import verify_checkpoint

    spec = read_json(PROBES[os.environ["RECOVERAGENT_S2_EXECUTION"]])
    cfg = configuration()
    actor = str(args.actor.resolve())
    optimizer = str(args.optimizer.resolve())
    receipt = verify_checkpoint(actor, cfg["models"][cfg["selected_model"]])
    if (
        receipt["adapter_sha256_after"] != spec["actor_sha256"]
        or sha256(Path(optimizer)) != spec["optimizer_sha256"]
    ):
        raise ValueError("Use the backed-up valid 15-update checkpoint and optimizer")
    if spec["task_id"] not in cfg["feedback_task_ids"]:
        raise ValueError("Probe task is outside frozen train100")
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(
        args.output / "plan.json",
        {
            **spec,
            "actor": actor,
            "optimizer": optimizer,
            "config": cfg,
            "protocols": protocol_identity(),
            "formal_training_authorized": False,
            "automatic_resume": False,
        },
    )


def sample(args):
    from recoveragent.s2_backend import BackendRuntime
    from recoveragent.v2_grpo import group_advantages
    from recoveragent.s2_schedule import GroupFeedback

    plan = frozen(args.output)
    deployment = read_json(args.deployment)
    # Never infer a new authorization from the old deployment's expired deadline.
    if not time.time() < args.deadline_epoch <= time.time() + plan["max_probe_seconds"]:
        raise ValueError("Supply an authorized future deadline at most two hours away")
    deployment["deadline_epoch"] = args.deadline_epoch
    target = args.output / "sampling"
    target.mkdir(exist_ok=False)
    evidence(target / "deployment.json", deployment)
    runtime = BackendRuntime(plan["config"], deployment, target)
    rows, started = [], time.monotonic()
    try:
        runtime.prepare_rollouts(plan["actor"])
        for i, seed in enumerate(plan["seeds"]):
            row = runtime.rollout(
                plan["actor"],
                {"task_id": plan["task_id"]},
                target / f"member_{i}",
                seed,
            )
            rows.append(row)
            atomic(target / "results.json", rows)
    finally:
        runtime.stop()
    advantages = group_advantages(rows)
    feedback = GroupFeedback(plan["config"]["task_evolution"]["signals"])
    event = feedback.observe(0, rows)
    write_json(
        target / "group.json",
        {
            "episodes": rows,
            "advantages": advantages,
            "feedback": event,
            "wall_seconds": time.monotonic() - started,
            "optimizer_updates": 0,
        },
    )


def compare(args):
    from recoveragent.grpo_compare import compare_outputs

    plan = frozen(args.output)
    deployment = read_json(args.deployment)
    if (
        not time.time()
        < args.deadline_epoch
        <= time.time() + plan["max_compare_seconds"]
    ):
        raise ValueError(
            "Supply an authorized comparison deadline at most two hours away"
        )
    group = read_json(args.output / "sampling/group.json")["episodes"]
    target = args.output / "comparison"
    target.mkdir(exist_ok=False)
    request = {
        "config": plan["config"],
        "groups": [group],
        "actor": plan["actor"],
        "actor_sha256": plan["actor_sha256"],
    }
    write_json(target / "request.json", request)
    for side in ("per_reply", "merged"):
        # Each subprocess loads the original weights independently; no optimizer step.
        command = [
            deployment["training_python"],
            str(Path(__file__).resolve()),
            "--execution",
            os.environ["RECOVERAGENT_S2_EXECUTION"],
            "worker",
            "--output",
            str(target / side),
            "--request",
            str(target / "request.json"),
            "--side",
            side,
        ]
        with (target / (side + ".log")).open("w") as log:
            subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=max(1, args.deadline_epoch - time.time()),
            )
    report = compare_outputs(target)
    write_json(target / "comparison.json", report)


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("--execution", choices=PROBES, default=S2_APPEND_EXECUTION)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("prepare", "sample", "compare", "worker"):
        q = sub.add_parser(name)
        q.add_argument("--output", required=True, type=Path)
        if name == "prepare":
            q.add_argument("--actor", required=True, type=Path)
            q.add_argument("--optimizer", required=True, type=Path)
        elif name in {"sample", "compare"}:
            q.add_argument("--deployment", required=True, type=Path)
            q.add_argument("--deadline-epoch", required=True, type=float)
        else:
            q.add_argument("--request", required=True, type=Path)
            q.add_argument("--side", choices=("per_reply", "merged"), required=True)
    args = p.parse_args()
    os.environ["RECOVERAGENT_S2_EXECUTION"] = args.execution
    if args.command == "worker":
        from recoveragent.grpo_compare import worker
        from recoveragent.v2_runtime import verify_lock, S2_RUNTIME

        verify_lock(S2_RUNTIME)
        worker(read_json(args.request), args.output, merged=args.side == "merged")
    else:
        globals()[args.command](args)


if __name__ == "__main__":
    main()
