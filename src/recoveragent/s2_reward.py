"""Student reward-time semantic checks; reuse the bounded Teacher text transport."""

import copy
import fcntl
from pathlib import Path

from .coevolve_goal import finish_semantic, SEMANTIC_PROMPT
from .io import read_json, write_json


def finalize(result, judge):
    evaluation = result.get("evaluation") or {}
    if evaluation.get("status") != "needs_semantic" or result.get("fault"):
        return result
    result = copy.deepcopy(result)
    try:
        evaluation = finish_semantic(evaluation, judge(evaluation["semantic_input"]))
        result.update(
            evaluation=evaluation,
            success=evaluation["success"],
            reward_valid=True,
            classification="success" if evaluation["success"] else "natural_failure",
            failure_origin=None if evaluation["success"] else "model",
        )
    except (ValueError, KeyError, TypeError, RuntimeError, OSError) as exc:
        result.update(
            success=None,
            reward_valid=False,
            classification="scoring_gap",
            failure_origin="scorer",
            scoring_fault={"reason": str(exc), "type": type(exc).__name__},
        )
    return result


def score(runtime, result, directory):
    from .s2_evolution import GroupEvolution
    from .v2_runtime import s2_execution
    from .v2_round import isolated

    settings = s2_execution()
    limits = settings.get("reward_judge_budget")
    if not limits:
        return finalize(
            result,
            lambda _: (_ for _ in ()).throw(
                RuntimeError("No authorized finite reward-judge budget")
            ),
        )
    root = runtime.output / "reward_judgments"
    root.mkdir(parents=True, exist_ok=True)
    # Both rollout threads share the finite reservation ledger, independent of
    # background generation. A lost request keeps its reservation, no paid retry.
    with (root / "budget.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        cfg = copy.deepcopy(runtime.cfg)
        cfg["s2"]["teacher_budget"] = limits
        key = "teacher_usage_priced_" + cfg["teacher"]["pricing"]["currency"].lower()
        engine = GroupEvolution(
            cfg,
            runtime.identity,
            {**runtime.deployment, "teacher_budget_amount": limits["priced_cost"]},
            root,
            {key: 0},
            runtime.deadline,
            isolated,
            budget_root=root,
        )
        engine.priced_cost_limit = True
        # Episode paths can share member names; bind to the complete path.
        from .io import digest

        target = root / digest(str(Path(directory).resolve()))[:24]
        if (target / "s2_operation.json").exists():
            if (target / "reward_result.json").exists():
                return read_json(target / "reward_result.json")
            return finalize(
                result,
                lambda _: (_ for _ in ()).throw(
                    RuntimeError("Interrupted semantic check; reservation retained")
                ),
            )
        before = engine.usage()
        judged = finalize(
            result,
            lambda content: engine.text(
                result["task_id"], target, SEMANTIC_PROMPT, content
            ),
        )
        after = engine.usage()
        judged["reward_judge_usage"] = {k: after[k] - before[k] for k in after}
        write_json(target / "reward_result.json", judged)
        return judged
