"""Synthetic evidence/transport only; no model, AppWorld execution or GPU."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from recoveragent import coevolve_llm as llm, s2_reward
from recoveragent.io import digest, read_json, write_json
from recoveragent.s2_experiment import require_pool_protocol
from recoveragent.s2_schedule import GroupFeedback
from recoveragent.v2_grpo import group_advantages
from recoveragent.v2_runtime import (
    S2_GENERATION_FOCUS_EXECUTION,
    S2_RUNTIME,
    verify_lock,
)
from test_coevolve_light_llm import admitted_task, new_task, reference, judge_reply
from test_s2_experiment import cfg, make_group


@pytest.fixture
def accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GENERATION_FOCUS_EXECUTION)
    return admitted_task(new_task(), tmp_path)


def observed(task, initial="clean"):
    return llm.pending(
        task,
        reference(task)["messages"],
        {"status": "success", "answer": 0},
        initial_state_hash=initial,
    )


def test_reference_is_control_side_and_bound_to_actual_task(accepted):
    evaluation = observed(accepted)
    content = evaluation["judge_input"]
    assert content["reference"] == llm.solver_evidence(
        accepted["validation"]["pending"]["judge_input"]
    )
    assert accepted["validation"]["pending"]["judge_input"]["reference"] is None
    assert evaluation["reference_identity"]["solver_trajectory_sha256"] == digest(
        reference(accepted)
    )
    assert evaluation["execution_identity"]["generated_task_id"] == accepted["task_id"]
    from recoveragent.v2_generator import public_task

    public = public_task({"instruction": "original", "supervisor": {}}, accepted)
    assert set(public) == {"instruction", "supervisor"}
    assert public["instruction"] == accepted["instruction"]


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "null_proof",
        "null_pending",
        "null_input",
        "initial",
        "task",
        "source",
        "sha",
        "facts",
    ],
)
def test_missing_or_mismatched_reference_is_invalid_before_judge(accepted, damage):
    task = copy.deepcopy(accepted)
    initial = "clean"
    if damage == "missing":
        task.pop("validation")
    elif damage == "null_proof":
        task["validation"] = None
    elif damage == "null_pending":
        task["validation"]["pending"] = None
    elif damage == "null_input":
        task["validation"]["pending"]["judge_input"] = None
    elif damage == "initial":
        initial = "other initial state"
    elif damage == "task":
        task["instruction"] += " Add another explicit requirement."
    elif damage == "source":
        task["source_task_id"] = cfg()["feedback_task_ids"][1]
    elif damage == "sha":
        task["validation"]["reference_sha256"] = "changed"
    else:
        task["validation"]["pending"]["judge_input"]["execution"][0]["observation"] = (
            "changed"
        )
    result = observed(task, initial)
    assert result["status"] == "scoring_gap" and not result["reward_valid"]
    assert result["success"] is None and "reference" in result["reason"].lower()
    raw = {"evaluation": result, "success": None, "reward_valid": False}
    assert (
        s2_reward.finalize(raw, lambda _: pytest.fail("no paid invalid check")) == raw
    )


@pytest.mark.parametrize("answer", [True, False, None])
def test_tristate_reaches_rewards_and_group_filter(accepted, tmp_path, answer):
    result = s2_reward.finalize(
        {"evaluation": observed(accepted), "fault": None},
        lambda _: judge_reply(answer),
    )
    assert result["success"] is answer and result["reward_valid"] is (
        answer is not None
    )
    assert result["classification"] == (
        "scoring_gap" if answer is None else "success" if answer else "natural_failure"
    )
    if answer is None:
        assert result["evaluation"]["judgment"]["success"] is None
        assert result["scoring_fault"]["type"] == "JudgeIndeterminate"
    rows = make_group(tmp_path, "row", [True, False, True, False])
    rows[2].update(result)
    feedback = GroupFeedback(cfg()["task_evolution"]["signals"]).observe("group", rows)
    assert feedback["complete"] is (answer is not None)
    assert (group_advantages(rows) is None) is (answer is None)
    if answer is None:
        assert not feedback["signals"]


def test_student_cannot_use_missing_reference_even_with_successful_solver(
    accepted, tmp_path
):
    admission = accepted["validation"]["pending"]
    engine = SimpleNamespace(text=lambda *a, **k: pytest.fail("network must not run"))
    with pytest.raises(llm.JudgeError, match="requires an admitted"):
        llm.judge(
            engine,
            accepted["source_task_id"],
            admission,
            tmp_path,
            purpose="student_reward",
        )
    evaluation = observed(accepted)
    evaluation["judge_input"]["reference"]["final_submission"]["answer"] = 999
    evaluation["input_sha256"] = digest(evaluation["judge_input"])
    with pytest.raises(llm.JudgeError, match="reference identity"):
        llm.judge(
            engine,
            accepted["source_task_id"],
            evaluation,
            tmp_path,
            purpose="student_reward",
        )


def test_null_solver_admission_is_deferred_not_admitted(accepted, tmp_path):
    task = new_task()
    path = tmp_path / "solver_unknown.json"
    write_json(path, reference(task))
    engine = SimpleNamespace(text=lambda *a, **k: judge_reply(None))
    with pytest.raises(llm.JudgeError, match="indeterminate"):
        llm.admit(engine, task, tmp_path / "unknown", reference_path=path)
    result = read_json(tmp_path / "unknown/admission_result.json")
    assert result["success"] is None and not result["reward_valid"]
    assert not (tmp_path / "unknown/accepted_task.json").exists()


def test_old_pool_and_old_source_lock_cannot_resume(accepted, monkeypatch):
    require_pool_protocol({"scoring_revision": llm.REVISION, "pool": [accepted]})
    with pytest.raises(ValueError, match="new LLM"):
        require_pool_protocol(
            {"scoring_revision": "coevolve_llm_reward_v1", "pool": []}
        )
    from recoveragent.v2_runtime import S2_EVOLVE_ROUND1_EXECUTION

    old = Path(f"configs/{S2_EVOLVE_ROUND1_EXECUTION}_lock.json").read_bytes()
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_EVOLVE_ROUND1_EXECUTION)
    with pytest.raises(ValueError, match="source lock|source changed"):
        verify_lock(S2_RUNTIME)
    assert Path(f"configs/{S2_EVOLVE_ROUND1_EXECUTION}_lock.json").read_bytes() == old
