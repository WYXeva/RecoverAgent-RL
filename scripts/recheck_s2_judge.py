"""Rejudge ten frozen evidence records once; never execute environments or train."""

import argparse
import copy
import fcntl
from pathlib import Path
import time

from recoveragent.coevolve_llm import call_identity, judge
from recoveragent.io import digest, read_json, write_json
from recoveragent.s2_evolution import GroupEvolution
from recoveragent.s2_experiment import configuration
from recoveragent.s2_reward import finalize
from recoveragent.s2_state import evidence, protocol_identity
from recoveragent.v2_runtime import s2_execution, verify_lock, S2_RUNTIME
from recoveragent.v2_tasks import ROOT, context_for


def run(plan_path, output):
    plan = read_json(plan_path)
    recipe = read_json(ROOT / "configs/s2_judge_thinking_check_20261117.json")
    if (
        digest(plan) != recipe["plan_sha256"]
        or [r["id"] for r in plan["cases"]] != recipe["case_ids"]
        or len(plan["cases"]) != recipe["calls_max"]
        or call_identity() != recipe["judge_identity"]
    ):
        raise ValueError("Fixed ten-case Judge evidence/configuration changed")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "controller.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cfg = copy.deepcopy(configuration())
        limits = s2_execution()["judge_recheck_budget"]
        cfg["s2"]["teacher_budget"] = limits
        evidence(
            output / "binding.json",
            {
                "plan_sha256": digest(plan),
                "source": verify_lock(S2_RUNTIME),
                "protocols": protocol_identity(),
                "budget": limits,
            },
        )
        started_path = output / "started.json"
        if not started_path.exists():
            write_json(started_path, {"epoch": time.time()})
        started = read_json(started_path)["epoch"]
        engine = GroupEvolution(
            cfg,
            cfg["models"][cfg["selected_model"]],
            {
                "appworld_root": "not-used-judge-only",
                "teacher_budget_amount": limits["priced_cost"],
            },
            output,
            {"teacher_usage_priced_usd": 0},
            time.monotonic() + started + limits["seconds"] - time.time(),
            None,
            budget_root=output,
        )
        rows = []
        for case in plan["cases"]:
            context_for(case["source_task_id"], training=True)
            target = output / case["id"]
            result_path = target / "result.json"
            if result_path.exists():
                result = read_json(result_path)
            else:
                result = finalize(
                    {"evaluation": case["evaluation"], "fault": None},
                    lambda _: judge(
                        engine,
                        case["source_task_id"],
                        case["evaluation"],
                        target / "judge",
                        purpose=case["purpose"],
                    ),
                )
                write_json(result_path, result)
            rows.append({"id": case["id"], "result": result})
            print(case["id"], result["success"], result["reward_valid"], flush=True)
        summary = {
            "results": rows,
            "usage": engine.usage(),
            "elapsed_seconds": time.time() - started,
            "new_task_executions": 0,
            "training_updates": 0,
        }
        if not (output / "summary.json").exists():
            write_json(output / "summary.json", summary)
        return read_json(output / "summary.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.input, args.output)["usage"])
