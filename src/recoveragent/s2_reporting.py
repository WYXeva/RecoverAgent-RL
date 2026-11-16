"""Small JSON/Markdown summaries from existing S2 receipts, no monitoring service."""

from collections import Counter
from pathlib import Path

from .io import read_json
from .s2_state import atomic


def score(rows):
    counts = Counter(
        "success"
        if r["success"] is True
        else "failure"
        if r["success"] is False
        else "unknown"
        for r in rows
    )
    return {k: counts[k] for k in ("success", "failure", "unknown")}


def evaluation_cost(rows):
    return {
        **{
            key: sum(r["usage"].get(key, 0) for r in rows)
            for key in (
                "model_requests",
                "prompt_tokens",
                "generated_tokens",
                "unknown_generation_reservation",
            )
        },
        **{
            key: sum(r.get("costs", {}).get(key, 0) for r in rows)
            for key in ("model_seconds", "environment_seconds")
        },
        "episode_seconds_known": sum(r.get("wall_seconds") or 0 for r in rows),
        "billed_cost": None,
    }


def paired(baseline, rows):
    old = {r["task_id"]: r["success"] for r in baseline}
    return {
        "gains": [
            r["task_id"]
            for r in rows
            if old[r["task_id"]] is False and r["success"] is True
        ],
        "regressions": [
            r["task_id"]
            for r in rows
            if old[r["task_id"]] is True and r["success"] is False
        ],
        "unknown_pairs": [
            r["task_id"]
            for r in rows
            if old[r["task_id"]] is None or r["success"] is None
        ],
    }


def generation_counts(batches):
    rejected = Counter()
    for batch in batches:
        for context in batch["contexts"]:
            if context.get("error"):
                rejected[context["error"]["category"]] += 1
            for attempt in context.get("attempts", []):
                if not attempt.get("accepted"):
                    rejected[attempt.get("rejection_category", "unknown")] += 1
    return {
        "batches": len(batches),
        **{
            key: sum(b[key] for b in batches)
            for key in (
                "selected_contexts",
                "actual_contexts",
                "explorations",
                "candidate_count",
                "accepted",
            )
        },
        "rejected_operations_by_reason": dict(rejected),
    }


def summarize_round(state, round_index, baseline):
    groups = [
        read_json(p) for p in state["groups"][round_index * 40 : (round_index + 1) * 40]
    ]
    categories = {
        k: Counter() for k in ("original", "initial_generated", "new_generated")
    }
    usage = Counter()
    examples = []
    for group in groups:
        outcomes = [e["success"] for e in group["episodes"]]
        category = (
            "exception"
            if any(type(x) is not bool for x in outcomes)
            or not group["event"]["complete"]
            else "0/4"
            if not any(outcomes)
            else "4/4"
            if all(outcomes)
            else "1-3/4"
        )
        categories[group["task"]["source"]][category] += 1
        for row in group["episodes"]:
            for key in (
                "model_requests",
                "prompt_tokens",
                "generated_tokens",
                "unknown_generation_reservation",
            ):
                usage[key] += row["usage"].get(key, 0)
            usage["model_seconds"] += row.get("costs", {}).get("model_seconds", 0)
            usage["environment_seconds"] += row.get("costs", {}).get(
                "environment_seconds", 0
            )
            usage["episode_seconds_known"] += row.get("wall_seconds") or 0
        generated = group["task"].get("generated_task")
        if generated:
            lineage = state["lineage"].get(
                generated["task_id"], {"origin": "initial_verified_pool"}
            )
            examples.append(
                {
                    "task_id": generated["task_id"],
                    "public_goal": generated["instruction"],
                    "source_lineage": lineage,
                    "source_kind": group["task"]["source"],
                    "student_outcomes": outcomes,
                    "group_file": state["groups"][group["index"]],
                }
            )
    updates = [
        read_json(p)
        for p in state["updates"]
        if round_index * 40 < read_json(p)["groups_through"] <= (round_index + 1) * 40
    ]
    contributing = sorted(
        {
            t
            for u in updates
            for t in u.get("contributing_generated_task_ids", [])
            if u["updated"]
        }
    )
    trained = {
        g["task"]["generated_task"]["task_id"]
        for g in groups
        if g["task"]["source"] == "new_generated"
    }
    added = [
        t
        for t, lineage in state["lineage"].items()
        if lineage.get("round") == round_index
    ]
    end_state = state["round_evaluations"][str(round_index)]
    waiting = sorted(set(added) - set(end_state["trained_generated_through"]))
    evaluated = read_json(end_state["results"])
    prior_usage = (
        state["round_evaluations"]
        .get(str(round_index - 1), {})
        .get("teacher_usage", {})
    )
    teacher_usage = end_state["teacher_usage"]
    examples.sort(key=lambda e: (e["source_kind"] != "new_generated", e["task_id"]))
    for example in examples[:5]:
        directory = example["source_lineage"].get("context_directory")
        if directory and (Path(directory) / "context_result.json").exists():
            example["observed_learning_need"] = read_json(
                Path(directory) / "context_result.json"
            )
        else:
            example["observed_learning_need"] = None
    return {
        "round": round_index + 1,
        "groups": len(groups),
        "trajectories": len(groups) * 4,
        "trajectory_count_basis": "scheduled_attempt_slots; incomplete slots are not complete rollouts",
        "scored_trajectories": sum(
            type(e["success"]) is bool for g in groups for e in g["episodes"]
        ),
        "unknown_trajectory_slots": sum(
            e["success"] is None for g in groups for e in g["episodes"]
        ),
        "started_model_trajectories": sum(
            e["usage"].get("model_requests", 0) > 0
            for g in groups
            for e in g["episodes"]
        ),
        "reward_groups": {
            k: {label: v[label] for label in ("0/4", "1-3/4", "4/4", "exception")}
            for k, v in categories.items()
        },
        "new_accepted": len(added),
        "new_tasks_actually_trained": sorted(trained),
        "new_tasks_waiting_for_later_round": waiting,
        "optimizer_updates": sum(u["updated"] for u in updates),
        "generated_tasks_with_nonzero_advantage_and_update": contributing,
        "student_cost": dict(usage),
        "student_billed_cost": None,
        "optimizer_training_seconds": sum(
            u.get("training_wall_seconds", 0) for u in updates
        ),
        "teacher_cost_cumulative": teacher_usage,
        "teacher_cost_this_round": {
            k: v - prior_usage.get(k, 0) for k, v in teacher_usage.items()
        },
        "generation": generation_counts(
            [
                read_json(path)
                for end, path in state["feedback_windows"].items()
                if round_index * 40 < int(end) <= (round_index + 1) * 40
            ]
        ),
        "dev": score(evaluated),
        "dev_cost": evaluation_cost(evaluated),
        "paired_vs_s0": paired(baseline, evaluated),
        "cases": examples[:5],
        "case_count_available": len(examples),
    }


def best_round(round_evaluations):
    if len(round_evaluations) != 5:
        raise ValueError(
            "All five fixed dev evaluations must finish before checkpoint selection"
        )
    # Unknown scores remain unknown; select on confirmed successes / fixed 24.
    return min(
        round_evaluations.values(), key=lambda r: (-r["counts"]["success"], r["round"])
    )


def report(output, state, baseline):
    output = Path(output)
    rounds = [
        summarize_round(state, i, baseline)
        for i in range(5)
        if str(i) in state["round_evaluations"]
    ]
    value = {
        "mode": state["mode"],
        "s0_baseline": {"counts": score(baseline), "cost": evaluation_cost(baseline)},
        "completed_groups": len(state["groups"]),
        "rounds": rounds,
        "latest_actor": state["actor"],
        "last_round_dev": rounds[-1]["dev"] if rounds else None,
        "dev_success_curve": [r["dev"]["success"] / 24 for r in rounds],
        "optimizer_steps": state["optimizer_steps"],
        "generation": generation_counts(
            [read_json(p) for p in state["feedback_windows"].values()]
        ),
        "selected": best_round(state["round_evaluations"])
        if len(rounds) == 5
        else None,
    }
    atomic(output / "summary.json", value)
    lines = [
        f"# S2 {state['mode']} 私有过程报告",
        "",
        "|轮次|组数|更新|新增任务|实际训练新增题|dev成功/失败/未知|",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in rounds:
        s = r["dev"]
        lines.append(
            f"|{r['round']}|{r['groups']}|{r['optimizer_updates']}|{r['new_accepted']}|{len(r['new_tasks_actually_trained'])}|{s['success']}/{s['failure']}/{s['unknown']}|"
        )
        atomic(output / f"round_{r['round']:02d}_report.json", r)
    lines += [
        "",
        "具体反馈→公开生成目标→当前Student结果案例及证据路径见各轮JSON的cases；不足5个时按实际数量报告。原始轨迹/验收细节不进入公开仓库。",
        "",
    ]
    (output / "summary.md").write_text("\n".join(lines))
    return value
