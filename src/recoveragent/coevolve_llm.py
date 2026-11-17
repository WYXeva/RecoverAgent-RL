"""One explicit LLM reward contract for Solver admission and Student sampling."""

import copy
import subprocess
from pathlib import Path

from .coevolve_signals import interaction_triplets
from .io import digest, read_json, write_json
from .s2_state import evidence
from .v2_tasks import context_for
from .coevolve_prompts import JUDGE

REVISION = "coevolve_llm_reward_v2"
INPUT_REVISION = "solver_reference_input_v1"


class JudgeError(ValueError):
    """Unscored execution, never a model failure reward."""


def call_identity():
    from .v2_runtime import s2_execution

    spec = s2_execution().get("judge_text")
    if not spec:
        return None  # Historical receipts retain their original shape.
    if spec["prompt_sha256"] != digest(JUDGE):
        raise JudgeError("Frozen Judge prompt changed")
    return {"role": "judge", **copy.deepcopy(spec)}


def is_task(task):
    return task.get("scoring_revision") == REVISION


def candidate(proposal, records, context):
    from .coevolve_tasks import VERSION, identify

    if set(proposal) != {"query", "action_sequence", "completion_criterion"} or any(
        not isinstance(v, str) or not v.strip() for v in proposal.values()
    ):
        raise ValueError("Candidate needs exactly three nonempty text fields")
    if not records or len({r["task_id"] for r in records}) != 1:
        raise ValueError("Candidate requires one source initial environment")
    source = records[0]["task_id"]
    task = {
        "generator": VERSION,
        "scoring_revision": REVISION,
        "source_task_id": source,
        "source_context": context_for(source, training=True),
        "instruction": proposal["query"].strip(),
        "action_sequence": proposal["action_sequence"],
        "completion_criterion": proposal["completion_criterion"],
        "provenance": {
            "records": [
                {"trajectory_id": r["trajectory_id"], "sha256": digest(r)}
                for r in records
            ],
            "context_sha256": digest(context),
        },
        "official_benchmark_claim": False,
    }
    return {**task, "task_id": identify(task)}


def migrate(task):
    """A new candidate identity; preserve its real public task, not old rewards."""
    from .coevolve_tasks import VERSION, identify

    source = task["source_task_id"]
    result = {
        "generator": VERSION,
        "scoring_revision": REVISION,
        "source_task_id": source,
        "source_context": context_for(source, training=True),
        "instruction": task["instruction"],
        "action_sequence": task["action_sequence"],
        "completion_criterion": task["completion_criterion"],
        "provenance": {
            "parent_task_id": task["task_id"],
            "parent_task_sha256": digest(task),
        },
        "official_benchmark_claim": False,
    }
    return {**result, "task_id": identify(result)}


def validate_task(task, require_validation=True):
    if not is_task(task) or any(
        k in task for k in ("success_conditions", "goal_contract", "execution_contract")
    ):
        raise ValueError("LLM-scored task cannot mix legacy reward contracts")
    if any(
        not isinstance(task.get(k), str) or not task[k].strip()
        for k in ("instruction", "action_sequence", "completion_criterion")
    ):
        raise ValueError("Incomplete LLM task")
    if require_validation:
        proof = task.get("validation", {})
        if not isinstance(proof, dict):
            raise JudgeError("Task needs LLM admission record")
        pending = proof.get("pending", {})
        if not isinstance(pending, dict) or not isinstance(
            pending.get("judge_input"), dict
        ):
            raise JudgeError("Task needs LLM admission evidence")
        if (
            proof.get("revision") != REVISION
            or proof.get("passed") is not True
            or proof.get("sha256") != digest(proof.get("result"))
            or not proof.get("initial_state_hash")
            or proof.get("public_task_sha256") != digest(task["instruction"])
            or proof.get("pending", {}).get("judge_input", {}).get("query")
            != task["instruction"]
            or pending.get("input_revision") != INPUT_REVISION
            or pending.get("judge_input", {}).get("reference", "missing") is not None
            or pending.get("input_sha256") != digest(pending.get("judge_input"))
            or pending.get("execution_identity")
            != execution_identity(task, proof.get("initial_state_hash"))
            or not proof.get("reference_sha256")
            or proof["reference_sha256"] != pending.get("solver_trajectory_sha256")
            or proof.get("solver_evidence_sha256")
            != digest(solver_evidence(pending.get("judge_input", {})))
        ):
            raise JudgeError("Task needs LLM admission before training")
        final = finish(proof["pending"], proof["judgment"])
        if final != proof["result"] or final["success"] is not True:
            raise JudgeError("Invalid LLM admission receipt")


def execution_identity(task, initial):
    return {
        "generated_task_id": task["task_id"],
        "source_task_id": task["source_task_id"],
        "public_task_sha256": digest(task["instruction"]),
        "initial_state_hash": initial,
    }


def solver_evidence(content):
    return {
        "execution": copy.deepcopy(content.get("execution")),
        "final_submission": copy.deepcopy(content.get("final_submission")),
    }


def pending(task, messages, submission, *, initial_state_hash=None, admission=False):
    validate_task(task, require_validation=False)
    steps = []
    for i, row in enumerate(interaction_triplets(messages, "actual")):
        steps.append(
            {
                "step": i,
                "action": row["action"],
                "observation": row["observation"],
                "failed": row["failed"],
            }
        )
    content = {
        "query": task["instruction"],
        "reference": None,
        "execution": steps,
        "final_submission": submission,
    }
    reference_identity = None
    try:
        if not initial_state_hash:
            raise JudgeError("Missing execution initial state for Judge binding")
        if not admission:
            # Validate the admitted task, not merely the source environment ID.
            from .coevolve_tasks import validate_task as validate_generated

            validate_generated(task, task["source_task_id"])
            proof = task["validation"]
            if proof["initial_state_hash"] != initial_state_hash:
                raise JudgeError(
                    "Solver reference initial state differs from execution"
                )
            content["reference"] = solver_evidence(proof["pending"]["judge_input"])
            reference_identity = {
                **execution_identity(task, initial_state_hash),
                "solver_trajectory_sha256": proof["reference_sha256"],
                "solver_evidence_sha256": proof["solver_evidence_sha256"],
                "admission_sha256": digest(proof),
            }
    except (ValueError, KeyError, TypeError) as exc:
        return {
            "status": "scoring_gap",
            "success": None,
            "reward_valid": False,
            "evaluator": REVISION,
            "reason": "Solver reference unavailable/inapplicable: " + str(exc),
            "official_result_complete": False,
        }
    return {
        "status": "needs_llm_judge",
        "success": None,
        "reward_valid": False,
        "evaluator": REVISION,
        "input_revision": INPUT_REVISION,
        "execution_identity": execution_identity(task, initial_state_hash),
        "reference_identity": reference_identity,
        "judge_input": content,
        "input_sha256": digest(content),
        "official_result_complete": False,
    }


def finish(evaluation, judgment):
    validate_input(evaluation)
    if (
        evaluation.get("evaluator") != REVISION
        or evaluation.get("status") != "needs_llm_judge"
    ):
        raise JudgeError("Wrong LLM scoring identity")
    if (
        not isinstance(judgment, dict)
        or set(judgment) != {"success", "reason", "evidence_steps"}
        or judgment["success"] is not None
        and type(judgment["success"]) is not bool
        or not isinstance(judgment["reason"], str)
        or not judgment["reason"].strip()
        or not isinstance(judgment["evidence_steps"], list)
    ):
        raise JudgeError(
            "Judge must return boolean or null success, reason and evidence_steps array"
        )
    steps = judgment["evidence_steps"]
    available = {r["step"] for r in evaluation["judge_input"]["execution"]}
    if any(type(i) is not int or i not in available for i in steps) or len(
        steps
    ) != len(set(steps)):
        raise JudgeError(
            "Judge evidence_steps reference nonexistent or repeated action numbers"
        )
    return {
        **evaluation,
        "status": "scoring_gap" if judgment["success"] is None else "valid",
        "success": judgment["success"],
        "reward_valid": judgment["success"] is not None,
        **({"reason": judgment["reason"]} if judgment["success"] is None else {}),
        "judgment": judgment,
        "judge_prompt_sha256": digest(JUDGE),
        **({"judge_call_identity": call_identity()} if call_identity() else {}),
    }


def judge(engine, source, evaluation, directory, *, purpose):
    """Same transport, prompt and input for both purposes; cache exact evidence only."""
    validate_input(evaluation)
    if evaluation["execution_identity"]["source_task_id"] != source:
        raise JudgeError("Judge source does not match execution identity")
    if purpose == "admission" and evaluation["judge_input"]["reference"] is not None:
        raise JudgeError("Solver admission must not use an external reference")
    if purpose == "student_reward" and evaluation["judge_input"]["reference"] is None:
        raise JudgeError("Student Judge requires an admitted Solver reference")
    directory = Path(directory)
    evidence(
        directory / "judge_binding.json",
        {
            "revision": REVISION,
            "input_sha256": digest(evaluation),
            "prompt_sha256": digest(JUDGE),
            "purpose": purpose,
            **({"call_identity": call_identity()} if call_identity() else {}),
        },
    )
    output = directory / "judgment.json"
    if output.exists():
        return read_json(output)
    if (directory / "judge_claim.json").exists():
        raise JudgeError("Interrupted Judge call; no automatic paid repeat")
    write_json(directory / "judge_claim.json", {"input_sha256": digest(evaluation)})
    try:
        value = engine.text(
            source,
            directory / "request",
            JUDGE,
            evaluation["judge_input"],
            role="judge",
        )
    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
        write_json(
            directory / "judge_failure.json",
            {"type": type(exc).__name__, "reason": str(exc)},
        )
        raise JudgeError(str(exc)) from exc
    write_json(output, value)  # Keep malformed JSON structure as evidence, too.
    return value


def validate_input(evaluation):
    content = evaluation.get("judge_input", {})
    identity = evaluation.get("execution_identity", {})
    if (
        evaluation.get("evaluator") != REVISION
        or evaluation.get("input_revision") != INPUT_REVISION
        or set(content) != {"query", "reference", "execution", "final_submission"}
        or evaluation.get("input_sha256") != digest(content)
        or identity.get("public_task_sha256") != digest(content.get("query"))
        or not identity.get("initial_state_hash")
    ):
        raise JudgeError("Judge input identity or evidence hash changed")
    reference = content["reference"]
    binding = evaluation.get("reference_identity")
    if reference is None:
        if binding is not None:
            raise JudgeError("Unexpected reference identity for Solver admission")
    elif (
        not isinstance(reference, dict)
        or set(reference) != {"execution", "final_submission"}
        or not isinstance(binding, dict)
        or any(binding.get(k) != v for k, v in identity.items())
        or binding.get("solver_evidence_sha256") != digest(reference)
        or not binding.get("solver_trajectory_sha256")
        or not binding.get("admission_sha256")
    ):
        raise JudgeError("Solver reference identity or evidence hash changed")


def reference_evaluation(task, reference, *, expected_initial=None):
    terminal = reference.get("coevolve_terminal") or {}
    initial = reference.get("initial_state_hash")
    if (
        reference.get("status") != "valid"
        or not initial
        or expected_initial is not None
        and initial != expected_initial
        or terminal.get("instruction") != task["instruction"]
        or not isinstance(reference.get("messages"), list)
        or reference.get("task_id") != task["source_task_id"]
    ):
        raise JudgeError("Solver evidence unavailable/inapplicable; pending validation")
    evaluation = pending(
        task,
        reference["messages"],
        {"status": terminal.get("status"), "answer": terminal.get("answer")},
        initial_state_hash=initial,
        admission=True,
    )
    evaluation["solver_trajectory_sha256"] = digest(reference)
    return evaluation


def admit(engine, task, directory, *, reference_path=None, expected_initial=None):
    directory = Path(directory)
    validate_task(task, require_validation=False)
    if reference_path is None:
        solver = directory / (directory.name + "_solver")
        reference_path = solver / "trajectory.json"
        if not reference_path.exists():
            if solver.exists():
                raise JudgeError("Interrupted Solver; no automatic paid repeat")
            try:
                reference = engine.execute(
                    task["source_task_id"],
                    solver,
                    {"mode": "validation", "task": task, "scoring_revision": REVISION},
                )
            except subprocess.SubprocessError as exc:
                raise JudgeError(
                    "Solver execution infrastructure error; retain logs"
                ) from exc
        else:
            reference = read_json(reference_path)
    else:
        reference = read_json(reference_path)
    evaluation = reference_evaluation(
        task, reference, expected_initial=expected_initial
    )
    judgment = judge(
        engine,
        task["source_task_id"],
        evaluation,
        directory / "judge",
        purpose="admission",
    )
    result = finish(evaluation, judgment)
    evidence(directory / "admission_result.json", result)
    if not result["reward_valid"]:
        raise JudgeError("Solver admission indeterminate: " + judgment["reason"])
    if not result["success"]:
        raise ValueError("LLM admission rejected: " + judgment["reason"])
    task = copy.deepcopy(task)
    task["validation"] = {
        "revision": REVISION,
        "passed": True,
        "pending": evaluation,
        "judgment": judgment,
        "result": result,
        "sha256": digest(result),
        "initial_state_hash": reference["initial_state_hash"],
        "public_task_sha256": digest(task["instruction"]),
        "reference_sha256": digest(reference),
        "solver_evidence_sha256": digest(solver_evidence(evaluation["judge_input"])),
        "reference_path": str(reference_path),
    }
    evidence(directory / "accepted_task.json", task)
    return task
