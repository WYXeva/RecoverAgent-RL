"""Student reward-time semantic checks; reuse the bounded Teacher text transport."""

import copy
import fcntl
from pathlib import Path

from .coevolve_goal import finish_semantic, SEMANTIC_PROMPT
from .io import read_json, write_json


def finalize(result, judge):
    evaluation = result.get("evaluation") or {}
    if evaluation.get("status") not in {
        "needs_semantic",
        "needs_llm_judge",
    } or result.get("fault"):
        return result
    result = copy.deepcopy(result)
    try:
        if evaluation["status"] == "needs_llm_judge":
            from .coevolve_llm import finish

            evaluation = finish(evaluation, judge(evaluation["judge_input"]))
        else:
            evaluation = finish_semantic(
                evaluation, judge(evaluation["semantic_input"])
            )
        result.update(
            evaluation=evaluation,
            success=evaluation["success"],
            reward_valid=evaluation["reward_valid"],
            classification="scoring_gap"
            if not evaluation["reward_valid"]
            else "success"
            if evaluation["success"]
            else "natural_failure",
            failure_origin="scorer"
            if not evaluation["reward_valid"]
            else None
            if evaluation["success"]
            else "model",
        )
        if not evaluation["reward_valid"]:
            result["scoring_fault"] = {
                "reason": evaluation["reason"],
                "type": "JudgeIndeterminate",
            }
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
    limits = runtime.deployment.get("reward_judge_budget") or settings.get(
        "reward_judge_budget"
    )
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
        from .s2_state import evidence, protocol_identity

        target = root / digest(str(Path(directory).resolve()))[:24]
        evidence(
            target / "reward_binding.json",
            {"input_sha256": digest(result), "protocols": protocol_identity()},
        )
        if (target / "reward_result.json").exists():
            return read_json(target / "reward_result.json")
        if (target / "s2_operation.json").exists():
            return finalize(
                result,
                lambda _: (_ for _ in ()).throw(
                    RuntimeError("Interrupted semantic check; reservation retained")
                ),
            )
        before = engine.usage()
        if result["evaluation"]["status"] == "needs_llm_judge":
            from .coevolve_llm import judge

            judged = finalize(
                result,
                lambda _: judge(
                    engine,
                    result["task_id"],
                    result["evaluation"],
                    target / "llm",
                    purpose="student_reward",
                ),
            )
        else:
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
