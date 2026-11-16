"""Review corrections reuse real-contract admission; all model calls are synthetic."""

import copy
import json

import pytest

from recoveragent.io import digest, read_json, write_json
from recoveragent.s2_state import atomic
from recoveragent import s2_review
from recoveragent.coevolve_tasks import validate_task
from test_coevolve import make_candidate, reference
from test_s2_evolution import engine


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", "v2_s2_review_20261117")
    e = engine(tmp_path / "engine")
    task, _, proposal = make_candidate()
    source, target = tmp_path / "original", tmp_path / "review"
    write_json(source / "candidate.json", task)
    write_json(source / "proposal.json", proposal)
    atomic(
        source / "acceptance.json",
        {
            "accepted": False,
            "reason": "Independent semantic verification did not pass",
        },
    )
    write_json(
        source / "semantic_judge/parsed.json",
        {
            "success": False,
            "reason": "Synthetic prior miscount",
        },
    )
    write_json(source / "original_solver/trajectory.json", reference(task))
    calls = []

    def text(source, directory, prompt, content):
        calls.append((directory, content))
        return {
            "success": True,
            "reason": "Actual observations satisfy all conditions",
            "original_error": "Miscounted the public observation",
            "evidence_refs": [{"rollout_id": "solver", "action_index": 1}],
        }

    def checks(task, path, directory):
        calls.append(("checks", str(path)))
        return [
            {
                "mode": mode,
                "initial_state_hash": "initial",
                "evaluation": {"success": mode == "positive"},
            }
            for mode in ("positive", "noop", "omit_completion", "wrong_answer")
        ]

    monkeypatch.setattr(e, "text", text)
    monkeypatch.setattr(e, "checks", checks)
    monkeypatch.setattr(
        e, "execute", lambda *a: pytest.fail("Replayed original Solver")
    )
    return e, source, target, calls


def test_semantic_correction_preserves_original_and_reuses_solver_once(case):
    e, source, target, calls = case
    before = {p: p.read_bytes() for p in source.rglob("*.json")}
    result = s2_review.review_candidate(e, source, target)
    assert result["status"] == "corrected"
    task = result["accepted"]
    validate_task(task, task["source_task_id"])
    assert task["execution_contract"]["expected_answer"] == "7"
    assert (
        task["generation_review"]["original_candidate_task_id"]
        == read_json(source / "candidate.json")["task_id"]
    )
    assert len(task["validation"]["results"]) == 4
    assert all(p.read_bytes() == value for p, value in before.items())
    assert s2_review.review_candidate(e, source, target) == result and len(calls) == 2
    atomic(source / "acceptance.json", {"accepted": True})
    with pytest.raises(ValueError, match="acceptance changed"):
        s2_review.review_candidate(e, source, target)


@pytest.mark.parametrize(
    "decision",
    [
        {"success": False, "reason": "Real failure"},
        {
            "success": True,
            "reason": "Unsupported",
            "original_error": "oops",
            "evidence_refs": [{"rollout_id": "invented", "action_index": 1}],
        },
        {"success": "true", "reason": "Wrong type"},
    ],
)
def test_real_failure_or_fabricated_evidence_never_admitted(
    case, monkeypatch, decision
):
    e, source, target, calls = case
    monkeypatch.setattr(e, "text", lambda *a: decision)
    result = s2_review.review_candidate(e, source, target)
    assert result["accepted"] is None
    assert not calls


def test_correction_cannot_bypass_negative_execution_checks(case, monkeypatch):
    e, source, target, _ = case
    monkeypatch.setattr(
        e, "checks", lambda *a: [{"mode": "positive", "evaluation": {"success": True}}]
    )
    assert s2_review.review_candidate(e, source, target)["accepted"] is None
    assert not (target / "accepted_task.json").exists()


def test_duplicate_recheck_uses_same_quality_and_full_solver_checks(case, monkeypatch):
    e, source, target, calls = case
    atomic(
        source / "acceptance.json",
        {"accepted": False, "quality_status": "duplicate_goal"},
    )
    quality = []
    monkeypatch.setattr(
        e, "quality", lambda task, path, **kw: quality.append(kw) or {"novel": True}
    )

    def execute(source, path, payload):
        result = reference(payload["task"])
        write_json(path / "trajectory.json", result)
        return result

    monkeypatch.setattr(e, "execute", execute)
    result = s2_review.review_candidate(e, source, target)
    assert result["status"] == "corrected" and quality == [{"review": True}]
    assert len(calls) == 2


def test_true_duplicate_retained_and_no_paid_repeat(case, monkeypatch):
    e, source, target, calls = case
    atomic(
        source / "acceptance.json",
        {"accepted": False, "quality_status": "duplicate_goal"},
    )

    def reject(*a, **kw):
        raise ValueError("Pure paraphrase")

    monkeypatch.setattr(e, "quality", reject)
    result = s2_review.review_candidate(e, source, target)
    assert result["accepted"] is None and "Pure paraphrase" in result["reason"]
    assert s2_review.review_candidate(e, source, target) == result and not calls


def test_interrupted_review_does_not_reissue_and_known_adjudication_skips(case):
    e, source, target, calls = case
    write_json(target / "claim.json", {})
    assert (
        s2_review.review_candidate(e, source, target)["status"] == "interrupted_review"
    )
    e.pool = [
        {
            "task_id": "corrected",
            "semantic_adjudication": {
                "original_candidate_task_id": read_json(source / "candidate.json")[
                    "task_id"
                ],
            },
        }
    ]
    assert (
        s2_review.review_candidate(e, source, target / "known")["status"]
        == "already_adjudicated"
    )
    assert not calls


def test_observation_counts_are_per_call_not_inferred_unique_total():
    base = {"rollout_id": "r", "action_index": 0, "failed": False}
    rows = [
        {
            **base,
            "observation": json.dumps(
                [{"id": 1, "amount": 0.1}, {"id": 2, "amount": 0.2}]
            ),
        }
    ]
    rows += [
        {**rows[0], "action_index": 1},
        {**base, "failed": True, "observation": "[]"},
    ]
    facts = s2_review.observation_facts(rows)
    assert len(facts) == 2
    assert all(f["row_count"] == 2 and f["amount_sum"] == "0.3" for f in facts)
    assert all("unique_total" not in f for f in facts)


def test_review_context_is_supplementary_immutable_and_idempotent(case):
    e, source, target, calls = case
    context = source.parent
    event = {"group_id": 7}
    original = {"lineage": {"group_id": 7}, "accepted": [], "candidate_count": 1}
    write_json(context / "s2_context_result.json", original)
    before = (context / "s2_context_result.json").read_bytes()
    result = s2_review.review_context(e, event, context, original)
    assert result["corrected_candidates"] == 1 and result["candidate_count"] == 0
    assert result["event_sha256"] == digest(event)
    assert s2_review.review_context(e, event, context, original) == result
    assert (
        len(calls) == 2 and (context / "s2_context_result.json").read_bytes() == before
    )


def test_adopted_update_waits_exact_worker_then_verifies_result(tmp_path, monkeypatch):
    import psutil
    from scripts.supervise_s2_background import adopted_update_ready
    from recoveragent import s2_background
    from recoveragent.demonstrations import sha256

    state = {
        "updated_through": 0,
        "pending": None,
        "actor": "parent",
        "optimizer": "opt",
        "optimizer_sha256": "sha",
        "optimizer_steps": 4,
        "groups": [],
    }
    for i in range(2):
        path = tmp_path / f"group{i}.json"
        write_json(path, {"episodes": [i]})
        state["groups"].append(str(path))
    request = {
        k: state[k]
        for k in ("actor", "optimizer", "optimizer_sha256", "optimizer_steps")
    }
    request.update(s0="S0", groups=[[0], [1]], group_ids=[0, 1])
    path = tmp_path / "attempt.json"
    write_json(
        path, {"request": request, "config": {}, "output": str(tmp_path / "result")}
    )
    record = {
        "groups_through": 2,
        "pid": 42,
        "create_time": 123,
        "payload": str(path),
        "payload_sha256": sha256(path),
    }
    monkeypatch.setattr(s2_background, "external_active", lambda *a: True)
    from types import SimpleNamespace

    monkeypatch.setattr(
        psutil,
        "Process",
        lambda pid: SimpleNamespace(
            cmdline=lambda: ["python", "update-worker", "--input", str(path)]
        ),
    )
    assert not adopted_update_ready(record, {}, {"s0": "S0"}, state)
    wrong = copy.deepcopy(state)
    wrong["actor"] = "different"
    with pytest.raises(ValueError, match="identity/configuration"):
        adopted_update_ready(record, {}, {"s0": "S0"}, wrong)
    monkeypatch.setattr(s2_background, "external_active", lambda *a: False)
    with pytest.raises(RuntimeError, match="without a verified result"):
        adopted_update_ready(record, {}, {"s0": "S0"}, state)
    atomic(
        tmp_path / "result/result.json",
        {"updated": True, "reload_passed": True, "optimizer_steps": 5},
    )
    assert adopted_update_ready(record, {}, {"s0": "S0"}, state)
    atomic(
        tmp_path / "result/result.json",
        {"updated": True, "reload_passed": False, "optimizer_steps": 5},
    )
    with pytest.raises(ValueError, match="reload/step"):
        adopted_update_ready(record, {}, {"s0": "S0"}, state)


def test_evolve_priority_before_static_final_continuation(tmp_path):
    from scripts.supervise_s2_background import continuation_mode

    atomic(tmp_path / "evolve/state.json", {"round_evaluations": [1] * 4})
    settings = {"finish_static_after_evolve": True}
    assert continuation_mode(tmp_path, settings) == "evolve"
    atomic(tmp_path / "evolve/state.json", {"round_evaluations": [1] * 5})
    assert continuation_mode(tmp_path, settings) == "both"
    assert continuation_mode(tmp_path, {}) == "evolve"
