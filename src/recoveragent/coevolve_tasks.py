"""General AppWorld tasks abstracted from real exploration, with executable rewards.

An independent Teacher solution and semantic check establish a reference.
Explicit Todoist goals use their public conditions and observed read-back;
legacy free-form tasks retain reference-state comparison. Neither evaluator
substitutes the source task's official score for the generated goal.
"""

import copy
import json
import re
from pathlib import Path

from .io import canonical, digest, write_json
from .v2_generator import apply_instruction, business_hash
from .v2_tasks import context_for
from .coevolve_signals import interaction_triplets

VERSION = "coevolve_appworld_task_v2"


def answer_comparison(instruction):
    """Public, narrow scalar contract; never infer IDs/text from the reference."""
    from .coevolve_answers import comparison
    from .completion_contract import MARKER, from_instruction

    if MARKER in instruction:
        return {
            "kind": "public_goal_output_v1",
            "output": from_instruction(instruction),
            "instruction_sha256": digest(instruction),
        }

    structured = comparison(instruction)
    if structured is not None:
        return structured
    from .answer_guard import public_contract

    public = public_contract(instruction)
    clause = re.search(
        r"\breport (?:only )?(?:the )?(?:total )?(?:number|count) of ([^.!?]+)[.!?]?$",
        instruction,
        re.IGNORECASE,
    )
    explicit_count = clause and not re.search(
        r"\b(and|or|format|text|digits|identifier|id|verbatim|rounded|percent|range)\b",
        clause[1],
        re.IGNORECASE,
    )
    return {
        "kind": "scalar_number"
        if public["kind"] in {"number", "money"} or explicit_count
        else "exact_json",
        "instruction_sha256": digest(instruction),
        "rule": public["rule"]
        if public["kind"] != "unclassified"
        else "explicit_final_report_count"
        if explicit_count
        else "no_unambiguous_scalar_contract",
    }


def answer_matches(actual, expected, comparison):
    if comparison["kind"] == "public_object_v1":
        from .coevolve_answers import matches

        return matches(actual, expected, comparison["fields"])
    if comparison["kind"] == "exact_json":
        return canonical(actual) == canonical(expected)
    from .answer_guard import numeric_representation

    # Reject booleans, units/currencies, leading-zero IDs and arbitrary text.
    def number(value):
        if isinstance(value, str) and not re.fullmatch(
            r"\s*-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\s*", value
        ):
            return None
        return numeric_representation(value)

    left, right = number(actual), number(expected)
    return left is not None and right is not None and left == right


def exploration_instruction(payload):
    if payload["mode"] == "validation":
        return payload["task"]["instruction"]
    from .coevolve_llm import REVISION

    if payload.get("scoring_revision") == REVISION:
        from .coevolve_prompts import EXPLORATION

        return EXPLORATION + "\nFeedback evidence: " + canonical(payload["context"])
    context = payload.get("context")
    guidance = (
        "Explore the available applications through their public APIs. Discover a useful, concrete multi-step user goal. "
        "Try meaningful combinations of operations, inspect results and correct observable errors. "
        "You may change state inside this isolated simulation. Never consult hidden task answers or evaluator internals. "
        "Build at least one successful sequence with three or more actions and a clear observable outcome. "
        "You are exploring capabilities, not solving the original source task. Finish by reporting the goal, steps and observations."
    )
    if context:
        guidance += " Target these evidence-based learning needs: " + canonical(context)
    else:
        guidance += " This is initial unguided exploration; choose a useful goal from observed capabilities."
    return guidance


def validation_hint(task):
    from .coevolve_llm import is_task

    if is_task(task):
        from .coevolve_prompts import SOLVER

        return SOLVER
    return (
        "Solve the assigned task in this fresh environment. The following is a proposed approach, not proof of success. "
        "Verify actual values and effects with tools; authenticate afresh. Complete only after the real goal is achieved. "
        + canonical(
            {
                "proposed_solution": task["action_sequence"],
                "completion_criterion": task["completion_criterion"],
            }
        )
    )


def capture_terminal(adapter, trajectory):
    current = adapter.world.requester.request(
        "supervisor", "show_active_task", track=False
    )
    if getattr(adapter, "llm_scoring", False):
        trajectory["coevolve_terminal"] = {
            k: current.get(k) for k in ("instruction", "status", "answer")
        }
        return
    trajectory["coevolve_terminal"] = {
        "instruction": current["instruction"],
        "status": current.get("status"),
        "answer": current.get("answer"),
        "business_sha256": business_hash(adapter),
    }
    task = getattr(adapter, "public_goal_task", None)
    if task:
        from .coevolve_goal import evidence, objective

        observed = evidence(adapter, task, trajectory["messages"])
        trajectory["goal_evidence"] = observed
        trajectory["coevolve_terminal"]["goal_evaluation"] = objective(task, observed)
    goal_task = getattr(adapter, "coevolve_goal_task", None)
    if goal_task:
        from .todoist_goal import satisfied

        trajectory["coevolve_terminal"]["goal_satisfied"] = satisfied(
            adapter, goal_task, trajectory["messages"]
        )


def identify(task):
    base = {k: v for k, v in task.items() if k not in {"task_id", "validation"}}
    return "coevolve_" + digest(base)[:24]


def candidate(proposal, explorations, context, *, scoring_revision=None):
    if scoring_revision is not None:
        from .coevolve_llm import REVISION, candidate as llm_candidate

        if scoring_revision != REVISION:
            raise ValueError("Unknown generated task scoring revision")
        return llm_candidate(proposal, explorations, context)
    if not explorations or len({r["task_id"] for r in explorations}) != 1:
        raise ValueError("Task abstraction requires one source initial environment")
    source = explorations[0]["task_id"]
    research = context_for(source, training=True)
    required = {"query", "action_sequence", "completion_criterion", "evidence_refs"}
    if not required <= proposal.keys() or any(
        not isinstance(proposal[k], str) or not proposal[k].strip()
        for k in required - {"evidence_refs"}
    ):
        raise ValueError("Incomplete model-generated task/solution/criterion")
    indexed = {}
    for record in explorations:
        for row in interaction_triplets(record["messages"], record["trajectory_id"]):
            indexed[(row["rollout_id"], row["action_index"])] = row
    references = proposal["evidence_refs"]
    if not isinstance(references, list) or len(references) < 3:
        raise ValueError("Task needs at least three real successful action references")
    chosen = []
    for ref in references:
        if (
            set(ref) != {"rollout_id", "action_index"}
            or type(ref["action_index"]) is not int
        ):
            raise ValueError("Malformed action evidence reference")
        row = indexed.get((ref["rollout_id"], ref["action_index"]))
        if row is None or row["failed"]:
            raise ValueError("Task references an invented or failed action")
        chosen.append(row)
    if len({(r["rollout_id"], r["action_index"]) for r in chosen}) < 3 or not any(
        not r["action"]["name"].startswith(("api_docs__", "supervisor__"))
        for r in chosen
    ):
        raise ValueError("Task lacks distinct business-action evidence")
    task = {
        "generator": VERSION,
        "source_task_id": source,
        "source_context": research,
        "instruction": proposal["query"].strip(),
        "action_sequence": proposal["action_sequence"],
        "completion_criterion": proposal["completion_criterion"],
        "evidence_refs": references,
        "exploration_sha256": [digest(r) for r in explorations],
        "context_sha256": digest(context),
        "official_benchmark_claim": False,
    }
    if proposal.get("goal_contract") is not None:
        from .todoist_goal import instruction

        task["goal_contract"] = copy.deepcopy(proposal["goal_contract"])
        task["instruction"] = instruction(task["goal_contract"])
    if "success_conditions" in proposal:
        from .coevolve_goal import validate
        from .completion_contract import render

        if "goal_contract" in task:
            raise ValueError("Use one goal scoring revision")
        task["success_conditions"] = copy.deepcopy(
            validate(proposal["success_conditions"])
        )
        task["instruction"] = render(
            task["instruction"], task["success_conditions"]["output"]
        )
    return {**task, "task_id": identify(task)}


def attach_reference(task, trajectory, judgment, *, scoring_repair=False):
    if judgment.get("success") is not True or not isinstance(
        judgment.get("reason"), str
    ):
        raise ValueError("Independent semantic verification did not pass")
    terminal = trajectory["coevolve_terminal"]
    if (
        not (
            trajectory["status"] == "valid"
            or (
                scoring_repair
                and "success_conditions" in task
                and trajectory.get("error_type") == "ScoringGap"
                and trajectory.get("scoring_gap")
                and not trajectory.get("interruption")
                and not trajectory.get("provider_error")
            )
        )
        or terminal["status"] != "success"
        or terminal["instruction"] != task["instruction"]
    ):
        raise ValueError("Reference did not actually finish the generated goal")
    task = copy.deepcopy(task)
    task["execution_contract"] = {
        "revision": VERSION,
        "initial_state_hash": trajectory["initial_state_hash"],
        "expected_business_sha256": terminal["business_sha256"],
        "expected_answer": terminal["answer"],
        "reference_trajectory_sha256": digest(trajectory),
        "semantic_judgment_sha256": digest(judgment),
        "answer_comparison": answer_comparison(task["instruction"]),
        "reward_scope": "exact_reference_business_state_and_answer_equivalence",
    }
    if "goal_contract" in task:
        from .todoist_goal import REVISION

        expected = len(task["goal_contract"]["tasks"])
        if terminal.get("goal_satisfied") is not True or not answer_matches(
            terminal["answer"],
            expected,
            task["execution_contract"]["answer_comparison"],
        ):
            raise ValueError("Reference did not satisfy the public Todoist goal")
        task["execution_contract"].update(
            revision=REVISION,
            expected_answer=expected,
            reward_scope="public_todoist_goal_and_readback",
        )
    if "success_conditions" in task:
        from .coevolve_goal import REVISION, objective, finish_semantic, ScoringGap

        if judgment.get("contract_complete") is not True:
            raise ScoringGap("Goal coverage has not been verified before admission")

        result = objective(task, trajectory["goal_evidence"])
        if result["status"] == "needs_semantic":
            result = finish_semantic(result, judgment["goal_semantic"])
        if result["success"] is not True or not result["reward_valid"]:
            raise ValueError("Solver did not satisfy frozen public goal conditions")
        task["execution_contract"].update(
            revision=REVISION,
            reward_scope="public_goal_conditions",
            goal_spec_sha256=digest(task["success_conditions"]),
            reference_goal_evaluation=result,
        )
    task["task_id"] = identify(task)
    return task


def validate_task(task, source_task_id, *, require_validation=True):
    if (
        task.get("generator") != VERSION
        or task["source_task_id"] != source_task_id
        or task["source_context"] != context_for(source_task_id, training=True)
        or task["task_id"] != identify(task)
    ):
        raise ValueError("CoEvolve generated task identity/source changed")
    from .coevolve_llm import is_task, validate_task as validate_llm

    if is_task(task):
        validate_llm(task, require_validation=require_validation)
        return
    revision = VERSION
    if "success_conditions" in task:
        from .coevolve_goal import REVISION, validate
        from .completion_contract import from_instruction

        revision = REVISION
        validate(task["success_conditions"])
        if (
            from_instruction(task["instruction"])
            != task["success_conditions"]["output"]
        ):
            raise ValueError("Public answer contract differs from goal scorer")
        if require_validation and task["execution_contract"][
            "goal_spec_sha256"
        ] != digest(task["success_conditions"]):
            raise ValueError("Frozen success conditions changed")
    if "goal_contract" in task:
        from .todoist_goal import REVISION, instruction

        revision = REVISION
        if task["instruction"] != instruction(task["goal_contract"]):
            raise ValueError("Todoist instruction differs from its goal contract")
    if require_validation:
        validation = task["validation"]
        if (
            not validation["passed"]
            or validation["sha256"] != digest(validation["results"])
            or task["execution_contract"]["revision"] != revision
            or task["execution_contract"]["answer_comparison"]
            != answer_comparison(task["instruction"])
        ):
            raise ValueError("CoEvolve task is not validated")
        check_results(task, validation["results"])


def evaluate(adapter, task, messages=None, *, initial_state_hash=None):
    from .coevolve_llm import is_task, pending

    if is_task(task):
        current = adapter.world.requester.request(
            "supervisor", "show_active_task", track=False
        )
        if current["instruction"] != task["instruction"]:
            raise ValueError("LLM scoring public task changed")
        return pending(
            task,
            messages or [],
            {"status": current.get("status"), "answer": current.get("answer")},
            initial_state_hash=initial_state_hash,
        )
    if "success_conditions" in task:
        from .coevolve_goal import evaluate as evaluate_goal

        result = evaluate_goal(adapter, task, messages or [])
        result["business_sha256_diagnostic"] = business_hash(adapter)
        return result
    contract = task["execution_contract"]
    if "goal_contract" in task:
        from .todoist_goal import REVISION, satisfied

        if contract["revision"] != REVISION:
            raise ValueError("Todoist goal uses a different scoring revision")
        state_matches = satisfied(adapter, task, messages or [])
    else:
        if contract["revision"] != VERSION:
            raise ValueError("Unknown generated-task scoring revision")
        state_matches = business_hash(adapter) == contract["expected_business_sha256"]
    current = adapter.world.requester.request(
        "supervisor", "show_active_task", track=False
    )
    success = (
        current.get("instruction") == task["instruction"]
        and current.get("status") == "success"
        and answer_matches(
            current.get("answer"),
            contract["expected_answer"],
            contract["answer_comparison"],
        )
        and state_matches
    )
    return {
        "status": "valid",
        "success": success,
        "evaluator": contract["revision"],
        "reference": "generated_initial_state",
        "official_result_complete": False,
    }


def validation_modes(task):
    if "success_conditions" in task:
        return {
            "positive": True,
            "noop": False,
            "omit_completion": False,
            **(
                {"wrong_answer": False}
                if task["success_conditions"]["output"]["required"]
                else {}
            ),
        }
    if "goal_contract" in task:
        from .todoist_goal import validation_modes as goal_modes

        return goal_modes(task["goal_contract"])
    return {
        "positive": True,
        "noop": False,
        "omit_completion": False,
        "wrong_answer": False,
    }


def check_results(task, results):
    modes = validation_modes(task)
    if (
        len(results) != len(modes)
        or {r["mode"] for r in results} != set(modes)
        or any(
            r["initial_state_hash"] != task["execution_contract"]["initial_state_hash"]
            or r["evaluation"]["success"] != modes[r["mode"]]
            for r in results
        )
    ):
        raise ValueError(
            "Generated goal failed independent positive/negative execution checks"
        )


def accept(task, results):
    check_results(task, results)
    return {
        **task,
        "validation": {"passed": True, "results": results, "sha256": digest(results)},
    }


def replay(adapter, task, trajectory, mode, output):
    from .tool_modes import execute_with_mode

    validate_task(task, adapter.task_id, require_validation=False)
    if mode not in validation_modes(task):
        raise ValueError("Unsupported validation mode")
    if digest(trajectory) != task["execution_contract"]["reference_trajectory_sha256"]:
        raise ValueError("Reference solution changed")
    apply_instruction(adapter, task["instruction"])
    from .todoist_goal import prepare, perturb

    prepare(adapter, task)
    if "success_conditions" in task:
        from .coevolve_goal import prepare as prepare_goal

        prepare_goal(adapter, task)
    initial = adapter.fingerprint()
    if initial != task["execution_contract"]["initial_state_hash"]:
        raise ValueError("Generated initial state changed")
    tools, tokens = adapter.initial_tools(), {}
    executed, changed = [], False
    if mode != "noop":
        for row in interaction_triplets(
            trajectory["messages"], trajectory["trajectory_id"]
        ):
            fn = row["action"]
            args = json.loads(fn["arguments"])
            if "goal_contract" in task:
                args, changed = perturb(
                    fn["name"], args, mode, task["goal_contract"], changed
                )
                if args is None:
                    continue
            if "access_token" in args:
                args["access_token"] = tokens.get(
                    args["access_token"], args["access_token"]
                )
            if fn["name"] == "supervisor__complete_task":
                if mode == "omit_completion":
                    continue
                if mode == "wrong_answer":
                    args["answer"] = "incorrect_negative_control_" + digest(
                        task["execution_contract"]["expected_answer"]
                    )
            result, tools = execute_with_mode(
                adapter, fn["name"], json.dumps(args), tools, "dynamic_discovery"
            )
            call_id = str(len(executed))
            executed.extend(
                [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "function": {
                                    "name": fn["name"],
                                    "arguments": json.dumps(args),
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": call_id, "content": result},
                ]
            )
            if (
                fn["name"].endswith("__login")
                and not row["failed"]
                and "Execution failed" not in result
            ):
                old, new = json.loads(row["observation"]), json.loads(result)
                if isinstance(old, dict) and "access_token" in old:
                    tokens[old["access_token"]] = new["access_token"]
    if (
        mode
        in {
            "omit_task",
            "wrong_due_date",
            "wrong_priority",
            "omit_readback",
            "extra_description",
        }
        and not changed
    ):
        raise ValueError("Validation did not exercise the requested business change")
    result = {
        "mode": mode,
        "initial_state_hash": initial,
        "evaluation": evaluate(adapter, task, executed),
    }
    write_json(Path(output) / "validation.json", result)
    return result
