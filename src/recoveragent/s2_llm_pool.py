"""One shared initial LLM-admitted pool; rejudge applicable saved Solver evidence."""

import copy
import fcntl
import time
from pathlib import Path

from .coevolve_llm import REVISION, JudgeError, admit, migrate, reference_evaluation
from .coevolve_tasks import validate_task
from .demonstrations import sha256
from .io import digest, read_json, write_json
from .s2_state import evidence, protocol_identity
from .v2_runtime import s2_execution


def manifest(output, kind):
    output = Path(output)
    value = {
        "revision": kind,
        "protocols": protocol_identity(),
        "files": {
            str(p.relative_to(output)): sha256(p)
            for p in sorted(output.rglob("*.json"))
            if p != output / "manifest.json"
        },
    }
    evidence(output / "manifest.json", value)
    return value


def verify(directory, kind):
    directory = Path(directory)
    proof = read_json(directory / "manifest.json")
    if proof.get("revision") != kind or proof.get("protocols") != protocol_identity():
        raise ValueError("Initial LLM pool protocol/manifest mismatch")
    for name, expected in proof["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(directory / name) != expected
        ):
            raise ValueError("Initial LLM pool evidence changed")
    return proof


def prepare_pool(legacy_directory, output, references=None):
    from .s2_inputs import load_bundle

    if s2_execution().get("generated_scoring_revision") != REVISION:
        raise ValueError("Select the lightweight LLM execution profile")
    legacy = load_bundle(legacy_directory)
    output = Path(output)
    paths = read_json(references) if references else {}
    candidates = []
    for old in legacy["pool"]:
        task = migrate(old)
        item = {
            "task": task,
            "parent_task_id": old["task_id"],
            "reference": None,
            "status": "deferred_validation",
        }
        path = paths.get(old["task_id"])
        if path:
            ref = read_json(path)
            try:
                contract = old["execution_contract"]
                if digest(ref) != contract["reference_trajectory_sha256"]:
                    raise JudgeError("Preserved Solver reference SHA mismatch")
                reference_evaluation(
                    task, ref, expected_initial=contract["initial_state_hash"]
                )
                relative = f"references/{old['task_id']}.json"
                evidence(output / relative, ref)
                item.update(
                    reference=relative,
                    expected_initial=contract["initial_state_hash"],
                    status="ready_for_one_judge",
                )
            except (ValueError, KeyError, TypeError) as exc:
                item["reason"] = str(exc)
        else:
            item["reason"] = (
                "No complete applicable saved Solver; do not automatically rerun Teacher"
            )
        candidates.append(item)
    feedback = []
    for row in legacy["feedback"]:
        relative = f"feedback/{row['task_id']}/history.json"
        evidence(output / relative, read_json(Path(row["directory"]) / "history.json"))
        feedback.append({"task_id": row["task_id"], "history": relative})
    prepared = {
        "scoring_revision": REVISION,
        "source_archive_sha256": legacy["archive_sha256"],
        "source_pool_sha256": digest(legacy["pool"]),
        "candidates": candidates,
        "feedback": feedback,
    }
    evidence(output / "prepared.json", prepared)
    manifest(output, "llm_pool_candidates_v1")
    return prepared


def admit_pool(cfg, deployment, prepared_directory, output, *, engine=None):
    """No Solver reruns. Missing evidence remains deferred; original slots fill gaps."""
    from .s2_evolution import GroupEvolution
    from .v2_round import isolated

    prepared_directory, output = Path(prepared_directory), Path(output)
    proof = verify(prepared_directory, "llm_pool_candidates_v1")
    prepared = read_json(prepared_directory / "prepared.json")
    if len(prepared["candidates"]) > 9:
        raise ValueError("Initial pool budget is limited to the frozen nine candidates")
    output.mkdir(parents=True, exist_ok=True)
    with (output / "admission.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (output / "bundle.json").exists():
            manifest(output, "llm_initial_pool_v1")
            return load(output)
        evidence(
            output / "preparation_binding.json",
            {"manifest_sha256": digest(proof), "protocols": protocol_identity()},
        )
        for name in proof["files"]:
            evidence(output / name, read_json(prepared_directory / name))
        limits = s2_execution()["initial_pool_judge_budget"]
        if engine is None:
            if (
                type(deployment.get("deadline_epoch")) not in (int, float)
                or deployment["deadline_epoch"] <= time.time()
            ):
                raise ValueError(
                    "Initial Judge admission requires a new authorized deadline"
                )
            copied = copy.deepcopy(cfg)
            copied["s2"]["teacher_budget"] = limits
            key = (
                "teacher_usage_priced_" + cfg["teacher"]["pricing"]["currency"].lower()
            )
            engine = GroupEvolution(
                copied,
                cfg["models"][cfg["selected_model"]],
                {**deployment, "teacher_budget_amount": limits["priced_cost"]},
                output,
                {key: 0, "feedback": []},
                time.monotonic() + deployment["deadline_epoch"] - time.time(),
                isolated,
                budget_root=output / "admission",
            )
        pool, results = [], []
        for item in prepared["candidates"]:
            target = output / "admission" / item["parent_task_id"]
            receipt = target / "result.json"
            if receipt.exists():
                result = read_json(receipt)
            else:
                result = {
                    "parent_task_id": item["parent_task_id"],
                    "accepted": False,
                    "status": "deferred_validation",
                }
                if item["reference"]:
                    if time.monotonic() + 120 >= engine.deadline:
                        raise TimeoutError(
                            "Initial pool Judge deadline reached; resume remaining candidates"
                        )
                    try:
                        task = admit(
                            engine,
                            item["task"],
                            target,
                            reference_path=output / item["reference"],
                            expected_initial=item["expected_initial"],
                        )
                        result.update(accepted=True, status="accepted", task=task)
                    except (ValueError, RuntimeError, OSError) as exc:
                        result.update(
                            status="invalid_judge"
                            if isinstance(exc, JudgeError)
                            else "not_admitted",
                            reason=str(exc),
                        )
                else:
                    result["reason"] = item["reason"]
                write_json(receipt, result)
            if result["accepted"]:
                pool.append(result["task"])
            results.append(result)
        bundle = {
            "scoring_revision": REVISION,
            "protocols": protocol_identity(),
            "archive_sha256": prepared["source_archive_sha256"],
            "pool": pool,
            "feedback": prepared["feedback"],
            "admission_results": results,
            "teacher_usage": engine.usage(),
            "solver_reruns": 0,
        }
        evidence(output / "bundle.json", bundle)
        manifest(output, "llm_initial_pool_v1")
        return load(output)


def load(directory):
    directory = Path(directory)
    verify(directory, "llm_initial_pool_v1")
    bundle = read_json(directory / "bundle.json")
    if (
        bundle.get("scoring_revision") != REVISION
        or bundle.get("protocols") != protocol_identity()
    ):
        raise ValueError("Initial pool uses another scoring protocol")
    for task in bundle["pool"]:
        validate_task(task, task["source_task_id"])
    bundle["feedback"] = [
        {
            "task_id": r["task_id"],
            "generated_task_id": None,
            "directory": str((directory / r["history"]).resolve().parent),
        }
        for r in bundle["feedback"]
    ]
    return bundle


def check_judge(cfg, deployment, plan_path, output):
    """Four frozen historical checks, separate labels; no new environment execution."""
    from .coevolve_llm import judge
    from .s2_reward import finalize
    from .s2_evolution import GroupEvolution
    from .v2_tasks import ROOT, context_for
    from .v2_round import isolated

    plan = read_json(plan_path)
    recipe = read_json(ROOT / "configs/s2_llm_judge_check_20261117.json")
    if digest(plan) != recipe["plan_sha256"] or len(plan["cases"]) != 4:
        raise ValueError("Historical Judge check differs from frozen evidence")
    if (
        type(deployment.get("deadline_epoch")) not in (int, float)
        or deployment["deadline_epoch"] <= time.time()
    ):
        raise ValueError("Judge check needs a new authorized deadline")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "controller.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        evidence(
            output / "binding.json",
            {"plan_sha256": digest(plan), "protocols": protocol_identity()},
        )
        limits = s2_execution()["judge_check_budget"]
        config = copy.deepcopy(cfg)
        config["s2"]["teacher_budget"] = limits
        key = "teacher_usage_priced_" + cfg["teacher"]["pricing"]["currency"].lower()
        engine = GroupEvolution(
            config,
            cfg["models"][cfg["selected_model"]],
            {**deployment, "teacher_budget_amount": limits["priced_cost"]},
            output,
            {key: 0, "feedback": []},
            time.monotonic() + deployment["deadline_epoch"] - time.time(),
            isolated,
            budget_root=output,
        )
        rows = []
        for case in plan["cases"]:
            source = case["source_task_id"]
            context_for(source, training=True)
            target = output / str(case["row"])
            result_path = target / "result.json"
            if result_path.exists():
                result = read_json(result_path)
            else:
                if time.monotonic() + 120 >= engine.deadline:
                    raise TimeoutError("Judge check deadline reached")
                result = finalize(
                    {"evaluation": case["evaluation"], "fault": None},
                    lambda _: judge(
                        engine,
                        source,
                        case["evaluation"],
                        target / "judge",
                        purpose="historical_check",
                    ),
                )
                result.update(
                    row=case["row"],
                    expected_human_review=case["expected_human_review"],
                    matches_review=result.get("reward_valid", False)
                    and result["success"] == case["expected_human_review"],
                )
                write_json(result_path, result)
            rows.append(result)
        summary = {
            "results": rows,
            "judge_usage": engine.usage(),
            "new_rollouts": 0,
            "training_updates": 0,
        }
        evidence(output / "summary.json", summary)
        return summary
