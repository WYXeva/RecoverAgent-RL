"""Synthetic reward-contract correction and no-replay handoff tests."""

import copy

import pytest

from recoveragent import coevolve_tasks as tasks
from recoveragent import v2_coevolve_resume as resume
from recoveragent.demonstrations import sha256
from recoveragent.io import digest, read_json, write_json
from recoveragent.v2_round import config
from recoveragent.v2_tasks import ROOT
from test_coevolve import make_candidate, reference


@pytest.mark.parametrize("actual", [3, 3.0, "3", "3.0"])
def test_explicit_count_numeric_equivalence_preserves_original_answer(actual):
    instruction = "Create the specified items, then list them and report the number of tasks created."
    contract = tasks.answer_comparison(instruction)
    assert contract["kind"] == "scalar_number"
    assert tasks.answer_matches(actual, "3", contract)
    assert contract["instruction_sha256"] == digest(instruction)


@pytest.mark.parametrize(
    "actual", [True, False, None, 4, "03", "3 tasks", "$3", "NaN", [3], {"count": 3}]
)
def test_count_rejects_wrong_values_types_and_units(actual):
    contract = tasks.answer_comparison("Report the number of tasks created.")
    assert not tasks.answer_matches(actual, "3", contract)


@pytest.mark.parametrize(
    "instruction",
    [
        "Return the ID verbatim.",
        "Return exactly the quoted text '3'.",
        "Report the number of digits in the ID as text.",
        "Report the count of tasks and names.",
        "Answer in the format 'Order <id>: $<amount>; Total: $<sum>'.",
    ],
)
def test_ambiguous_or_formatted_answers_keep_exact_contract(instruction):
    contract = tasks.answer_comparison(instruction)
    assert contract["kind"] == "exact_json"
    assert tasks.answer_matches("3", "3", contract)
    assert not tasks.answer_matches(3, "3", contract)


def test_upgrade_preserves_goal_state_and_reference_and_does_not_edit_source():
    candidate, _, _ = make_candidate()
    candidate["instruction"] = "Report the number of tasks created."
    candidate["task_id"] = tasks.identify(candidate)
    task = tasks.attach_reference(
        candidate, reference(candidate), {"success": True, "reason": "synthetic"}
    )
    task["generator"] = task["execution_contract"]["revision"] = (
        "coevolve_appworld_task_v1"
    )
    task["execution_contract"].pop("answer_comparison")
    task["task_id"] = tasks.identify(task)
    checks = [
        {
            "mode": m,
            "initial_state_hash": "initial",
            "evaluation": {"success": m == "positive"},
        }
        for m in ("positive", "noop", "omit_completion", "wrong_answer")
    ]
    old = tasks.accept(task, checks)
    untouched = copy.deepcopy(old)
    upgraded = resume.upgrade(old)
    assert old == untouched
    assert upgraded["task_id"] != old["task_id"]
    for name in (
        "instruction",
        "action_sequence",
        "completion_criterion",
        "source_task_id",
        "evidence_refs",
    ):
        assert upgraded[name] == old[name]
    for name in (
        "expected_answer",
        "expected_business_sha256",
        "reference_trajectory_sha256",
        "initial_state_hash",
    ):
        assert upgraded["execution_contract"][name] == old["execution_contract"][name]
    assert "validation" not in upgraded  # Must really run fresh validation first.
    damaged = copy.deepcopy(old)
    damaged["instruction"] = "A different task"
    with pytest.raises(ValueError):
        resume.upgrade(damaged)


@pytest.fixture
def stopped_source(tmp_path, monkeypatch):
    cfg = config()
    old = read_json(ROOT / "configs/v2_round1_coevolve_20261109.json")
    root, code, source, s1 = [
        tmp_path / n for n in ("root", "old_code", "source", "S1")
    ]
    monkeypatch.setattr(resume, "ROOT", root)
    write_json(root / "configs/v2_round1_coevolve_20261109.json", old)
    code.mkdir()
    (code / "source.py").write_text("synthetic original source")
    lock = {"files": {"source.py": sha256(code / "source.py")}}
    write_json(root / old["source_lock"], lock)
    write_json(code / old["source_lock"], lock)
    write_json(source / "run_config.json", old)
    deployment = {"job_deadline_epoch": 12345, "coevolve_source_directory": str(code)}
    write_json(
        source / "preflight.json",
        {
            "source": {"lock_sha256": sha256(root / old["source_lock"])},
            "deployment": deployment,
        },
    )
    identity = cfg["models"][cfg["selected_model"]]
    write_json(
        s1 / "checkpoint_receipt.json",
        {"model": identity, "adapter_sha256_after": "S1_sha"},
    )
    policy = {**identity, "checkpoint_id": digest({"adapter_sha256": "S1_sha"})}
    carried = {
        "feedback": [],
        "recovery": [],
        "teacher_usage_priced_cny": 1.0,
        "training_generated_tokens": 10,
        "training_generated_token_reservation": 4,
    }
    summary = {
        **carried,
        "status": "stopped",
        "real_checkpoints": {"S1_recovery": str(s1)},
        "training_generated_tokens": 12,
        "teacher_usage_priced_cny": 1.2,
    }
    write_json(source / "round_summary.json", summary)
    write_json(
        source / "answer_contract_handoff.json",
        {
            "reason": "generated_count_answer_string_vs_number_verifier_bug_before_first_GRPO_update",
            "optimizer_steps_before_stop": 0,
            "absolute_deadline": 12345,
        },
    )
    write_json(
        source / "coevolve/one/trajectory.json", {"usage": {"usage_priced_cost": 0.2}}
    )
    directory = source / "rollouts/group_00_member_0"
    write_json(
        directory / "result.json",
        {
            "policy": policy,
            "classification": "natural_failure",
            "purpose": "grpo",
            "generated_task_id": "generated_legacy",
            "usage": {"generated_tokens": 2},
        },
    )
    write_json(
        directory / "session/requests/0.request.json",
        {"policy": policy, "input_token_ids": [1]},
    )
    write_json(
        directory / "session/requests/0.response.json",
        {
            "policy": policy,
            "input_token_ids": [1],
            "token_ids": [2, 3],
            "logprobs": [-1.0, -2.0],
        },
    )
    return source, cfg, deployment, s1, carried


def test_completed_current_policy_prefix_and_costs_can_be_carried(stopped_source):
    summary, completed = resume.verify_source(*stopped_source)
    assert len(completed) == 1 and summary["training_generated_tokens"] == 12
    assert summary["teacher_usage_priced_cny"] == 1.2


@pytest.mark.parametrize(
    "damage",
    [
        "source",
        "recipe",
        "deadline",
        "update",
        "partial",
        "tokens",
        "unknown_cost",
        "policy",
    ],
)
def test_handoff_rejects_incompatible_or_partial_state(stopped_source, damage):
    source, cfg, deployment, s1, carried = stopped_source
    if damage == "source":
        (source.parent / "old_code/source.py").write_text("modified")
    elif damage == "recipe":
        cfg["student_limits"]["max_requests"] += 1
    elif damage == "deadline":
        deployment["job_deadline_epoch"] += 1
    elif damage == "update":
        write_json(source / "S2_grpo/updates/000.json", {"updated": True})
    elif damage == "partial":
        write_json(
            source / "rollouts/group_00_member_1/session/requests/0.request.json", {}
        )
    elif damage == "tokens":
        carried["training_generated_tokens"] += 1
    elif damage == "unknown_cost":
        write_json(source / "coevolve/text/unknown_usage.json", {})
    elif damage == "policy":
        p = source / "rollouts/group_00_member_0/result.json"
        r = read_json(p)
        r["policy"]["checkpoint_id"] = "changed"
        p.write_text(__import__("json").dumps(r))
    with pytest.raises(ValueError):
        resume.verify_source(source, cfg, deployment, s1, carried)
