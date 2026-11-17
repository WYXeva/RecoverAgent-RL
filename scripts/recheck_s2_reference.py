"""Rejudge frozen Solver/Student evidence; no environments, inference or training."""

import argparse
import copy
import fcntl
from pathlib import Path
import time

from recoveragent import coevolve_llm as llm
from recoveragent.coevolve_tasks import identify
from recoveragent.demonstrations import sha256
from recoveragent.io import digest, read_json, write_json
from recoveragent.s2_evolution import GroupEvolution
from recoveragent.s2_experiment import configuration
from recoveragent.s2_reward import finalize
from recoveragent.s2_state import evidence, protocol_identity
from recoveragent.v2_runtime import s2_execution, verify_lock, S2_RUNTIME


def frozen(record):
    value = read_json(record["path"])
    if digest(value) != record["sha256"]:
        raise ValueError("Saved evidence changed: " + record["path"])
    return value


def sources(item):
    old, solver = frozen(item["task"]), frozen(item["solver"])
    proof = old.get("validation", {})
    contract = old.get("execution_contract", {})
    initial = proof.get("initial_state_hash") or contract.get("initial_state_hash")
    reference_sha = proof.get("reference_sha256") or contract.get(
        "reference_trajectory_sha256"
    )
    if (
        not initial
        or old["task_id"] != identify(old)
        or digest(solver) != reference_sha
    ):
        raise ValueError("Preserved task/Solver binding changed")
    task = llm.migrate(old)
    llm.reference_evaluation(task, solver, expected_initial=initial)
    episodes = []
    for row in item["episodes"]:
        raw, history = frozen(row["result"]), frozen(row["history"])
        if (
            raw["task_id"] != old["source_task_id"]
            or raw["generated_task_id"] != old["task_id"]
            or raw["initial_state_hash"] != initial
        ):
            raise ValueError("Historical episode task/initial state mismatch")
        # Reuse the actually accepted submission, never raw attempts or labels.
        if "accepted_submission" in raw:
            submission = raw["accepted_submission"]
        else:
            # Older receipts lack accepted_submission; use the immutable terminal
            # audit tied to these exact original history/result files.
            terminal = frozen(row["terminal_audit"])
            for kind in ("history", "result"):
                expected = [
                    value
                    for path, value in terminal["original_files"].items()
                    if path.endswith("/" + kind + ".json")
                ]
                if expected != [sha256(row[kind]["path"])]:
                    raise ValueError("Terminal audit does not match original episode")
            submission = {
                "status": terminal["status"],
                "answer": terminal["actual_answer"],
            }
        episodes.append((row["id"], raw, history, submission))
    return task, initial, episodes


def run(plan_path, output):
    plan = read_json(plan_path)
    limits = s2_execution()["judge_recheck_budget"]
    if sum(1 + len(t["episodes"]) for t in plan["tasks"]) > limits["requests"]:
        raise ValueError("Fixed recheck exceeds its finite Judge budget")
    prepared = [sources(item) for item in plan["tasks"]]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "controller.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cfg = copy.deepcopy(configuration())
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
        if not (output / "started.json").exists():
            write_json(output / "started.json", {"epoch": time.time()})
        started = read_json(output / "started.json")["epoch"]
        engine = GroupEvolution(
            cfg,
            cfg["models"][cfg["selected_model"]],
            {
                "appworld_root": "unused-evidence-only",
                "teacher_budget_amount": limits["priced_cost"],
            },
            output,
            {"teacher_usage_priced_usd": 0},
            time.monotonic() + started + limits["seconds"] - time.time(),
            None,
            budget_root=output,
        )
        rows = []
        for item, (task, initial, episodes) in zip(
            plan["tasks"], prepared, strict=True
        ):
            target = output / item["id"]
            admitted = llm.admit(
                engine,
                task,
                target / "admission",
                reference_path=item["solver"]["path"],
                expected_initial=initial,
            )
            for name, raw, history, submission in episodes:
                path = target / name / "result.json"
                if path.exists():
                    result = read_json(path)
                else:
                    evaluation = llm.pending(
                        admitted,
                        history["messages"],
                        submission,
                        initial_state_hash=raw["initial_state_hash"],
                    )
                    result = finalize(
                        {
                            "evaluation": evaluation,
                            "fault": raw.get("fault"),
                            "success": None,
                            "reward_valid": False,
                        },
                        lambda _: llm.judge(
                            engine,
                            task["source_task_id"],
                            evaluation,
                            target / name / "judge",
                            purpose="student_reward",
                        ),
                    )
                    result["source_result_sha256"] = digest(raw)
                    write_json(path, result)
                rows.append({"task": item["id"], "case": name, "result": result})
                print(
                    item["id"],
                    name,
                    result["success"],
                    result["reward_valid"],
                    flush=True,
                )
        summary = {
            "rows": rows,
            "usage": engine.usage(),
            "elapsed_seconds": time.time() - started,
            "new_environment_executions": 0,
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
