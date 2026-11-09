"""General AppWorld tasks abstracted from real exploration, with executable rewards.

An independent Teacher solution and semantic check establish a reference. The
program reward checks exact answer and business-state equivalence to that real
solution. This conservative verifier may reject alternative valid end states;
it never substitutes the source task's official score for the generated goal.
"""

import copy
import json
from pathlib import Path

from .io import canonical, digest, write_json
from .v2_generator import apply_instruction, business_hash
from .v2_tasks import context_for
from .coevolve_signals import interaction_triplets

VERSION = "coevolve_appworld_task_v1"


def exploration_instruction(payload):
    if payload["mode"] == "validation":
        return payload["task"]["instruction"]
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
    trajectory["coevolve_terminal"] = {
        "instruction": current["instruction"],
        "status": current.get("status"),
        "answer": current.get("answer"),
        "business_sha256": business_hash(adapter),
    }


def identify(task):
    base = {k: v for k, v in task.items() if k not in {"task_id", "validation"}}
    return "coevolve_" + digest(base)[:24]


def candidate(proposal, explorations, context):
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
    return {**task, "task_id": identify(task)}


def attach_reference(task, trajectory, judgment):
    if judgment.get("success") is not True or not isinstance(
        judgment.get("reason"), str
    ):
        raise ValueError("Independent semantic verification did not pass")
    terminal = trajectory["coevolve_terminal"]
    if (
        trajectory["status"] != "valid"
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
        "reward_scope": "exact_reference_business_state_and_answer_equivalence",
    }
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
    if require_validation:
        validation = task["validation"]
        if (
            not validation["passed"]
            or validation["sha256"] != digest(validation["results"])
            or task["execution_contract"]["revision"] != VERSION
        ):
            raise ValueError("CoEvolve task is not validated")
        check_results(task, validation["results"])


def evaluate(adapter, task):
    contract = task["execution_contract"]
    current = adapter.world.requester.request(
        "supervisor", "show_active_task", track=False
    )
    success = (
        current.get("instruction") == task["instruction"]
        and current.get("status") == "success"
        and canonical(current.get("answer")) == canonical(contract["expected_answer"])
        and business_hash(adapter) == contract["expected_business_sha256"]
    )
    return {
        "status": "valid",
        "success": success,
        "evaluator": VERSION,
        "reference": "generated_initial_state",
        "official_result_complete": False,
    }


def check_results(task, results):
    modes = {"positive", "noop", "omit_completion", "wrong_answer"}
    if (
        len(results) != 4
        or {r["mode"] for r in results} != modes
        or any(
            r["initial_state_hash"] != task["execution_contract"]["initial_state_hash"]
            or r["evaluation"]["success"] != (r["mode"] == "positive")
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
    if digest(trajectory) != task["execution_contract"]["reference_trajectory_sha256"]:
        raise ValueError("Reference solution changed")
    apply_instruction(adapter, task["instruction"])
    initial = adapter.fingerprint()
    if initial != task["execution_contract"]["initial_state_hash"]:
        raise ValueError("Generated initial state changed")
    tools, tokens = adapter.initial_tools(), {}
    if mode != "noop":
        for row in interaction_triplets(
            trajectory["messages"], trajectory["trajectory_id"]
        ):
            fn = row["action"]
            args = json.loads(fn["arguments"])
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
            if (
                fn["name"].endswith("__login")
                and not row["failed"]
                and "Execution failed" not in result
            ):
                old, new = json.loads(row["observation"]), json.loads(result)
                if isinstance(old, dict) and "access_token" in old:
                    tokens[old["access_token"]] = new["access_token"]
    result = {
        "mode": mode,
        "initial_state_hash": initial,
        "evaluation": evaluate(adapter, task),
    }
    write_json(Path(output) / "validation.json", result)
    return result
