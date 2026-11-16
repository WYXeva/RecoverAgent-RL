"""Public multi-value answers and simplified-task admission; no paid calls."""

import copy
import json

import pytest

from recoveragent.coevolve_answers import prepare_proposal
from recoveragent.coevolve_generation import (
    Evolution,
    TaskQualityError,
    require_answer_contract,
)
from recoveragent.coevolve_tasks import answer_comparison, answer_matches
from recoveragent.io import read_json
import test_coevolve_quality

from test_s2_evolution import engine

quality_case = test_coevolve_quality.quality_engine


CART = "Find the highest-rated wireless mouse under $50 with at least 5 in stock. Add exactly 2 units to my cart, then tell me the cart's base cost, tax, and total cost."


def test_real_deferred_cart_request_gets_public_three_field_contract_without_changing_source():
    raw = {"query": CART}
    before = copy.deepcopy(raw)
    prepared = prepare_proposal(raw)
    require_answer_contract({"instruction": prepared["query"]})
    comparison = answer_comparison(prepared["query"])
    assert raw == before
    assert comparison["kind"] == "public_object_v1"
    assert comparison["fields"] == {
        "base_cost": "number",
        "tax": "number",
        "total_cost": "number",
    }
    assert answer_matches(
        '{"total_cost":22,"tax":2.0,"base_cost":20}',
        '{"base_cost":20.0,"tax":2,"total_cost":22.0}',
        comparison,
    )
    assert (
        answer_comparison(CART)["kind"] == "exact_json"
    )  # historical instruction is untouched


@pytest.mark.parametrize(
    "actual",
    [
        '{"base_cost":20,"tax":2}',
        '{"base_cost":20,"tax":2,"total_cost":22,"extra":1}',
        '{"base_cost":"$20","tax":2,"total_cost":22}',
        '{"base_cost":true,"tax":2,"total_cost":22}',
        '{"base_cost":20,"tax":2,"total_cost":23}',
        '{"base_cost":NaN,"tax":2,"total_cost":22}',
        '{"base_cost":20,"base_cost":21,"tax":2,"total_cost":22}',
        "Base cost: $20; tax: $2; total: $22",
    ],
)
def test_multi_value_rejects_wrong_keys_values_types_and_units(actual):
    comparison = answer_comparison(prepare_proposal({"query": CART})["query"])
    assert not answer_matches(
        actual, '{"base_cost":20,"tax":2,"total_cost":22}', comparison
    )


def test_explicit_fields_preserve_text_ids_and_do_not_guess_from_answers():
    prepared = prepare_proposal(
        {
            "query": "Find the mail and return its identifier and attachment count.",
            "answer_fields": {"email_id": "string", "attachment_count": "integer"},
        }
    )
    comparison = answer_comparison(prepared["query"])
    assert answer_matches(
        '{"email_id":"0012","attachment_count":1}',
        {"email_id": "0012", "attachment_count": 1},
        comparison,
    )
    assert not answer_matches(
        {"email_id": 12, "attachment_count": 1},
        {"email_id": "0012", "attachment_count": 1},
        comparison,
    )
    assert not answer_matches(
        {"email_id": "0012", "attachment_count": 1.0},
        {"email_id": "0012", "attachment_count": 1},
        comparison,
    )
    original = "Return the ID verbatim."
    assert prepare_proposal({"query": original}) == {"query": original}
    assert answer_comparison(original)["kind"] == "exact_json"


def test_simpler_subgoal_is_admissible_and_records_new_policy(
    quality_case, monkeypatch
):
    e, task, path = quality_case
    e.curriculum_admission = True
    task["instruction"] = "Find the latest order and return its ID verbatim."

    def judge(source, directory, prompt, content):
        assert "simpler variant is a valid new task" in prompt
        assert "practicing just one component" in prompt
        assert "PRIVATE_REFERENCE" not in json.dumps(content)
        return {
            "novel": True,
            "reason": "Practice identification only",
            "goal_difference": "Drops aggregation and keeps identifying the latest order",
            "duplicate_of": None,
        }

    monkeypatch.setattr(e, "text", judge)
    receipt = e.quality(task, path)
    assert receipt["revision"] == "coevolve_curriculum_quality_v2"
    assert receipt["passed"]


def test_feedback_alignment_gate_is_removed_without_a_shadow_teacher_call(
    tmp_path, monkeypatch
):
    e = engine(tmp_path)
    e.curriculum_admission = True
    monkeypatch.setattr(Evolution, "quality", lambda *args, **kwargs: {"passed": True})
    monkeypatch.setattr(
        e, "text", lambda *args: pytest.fail("Alignment review must not run")
    )
    assert e.quality(
        {"instruction": "Find only the email."}, tmp_path / "candidate"
    ) == {"passed": True}


def test_exact_unchanged_duplicate_is_still_rejected(quality_case, monkeypatch):
    e, task, path = quality_case
    e.curriculum_admission = True
    task["instruction"] = e.pool[0]["instruction"]
    monkeypatch.setattr(
        e, "text", lambda *args: pytest.fail("No call needed for identical goal")
    )
    with pytest.raises(TaskQualityError) as exc:
        e.quality(task, path)
    assert exc.value.quality_status == "duplicate_goal"


def test_explicit_new_policy_regeneration_preserves_old_batch_and_enqueues_once(
    tmp_path, monkeypatch
):
    from recoveragent.s2_background import enqueue_regeneration
    from recoveragent.io import write_json
    from test_s2_background import experiment
    from test_s2_experiment import FakeRuntime, cfg

    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", "v2_s2_admission_20261117")
    run = experiment(tmp_path, FakeRuntime(cfg()), mode="evolve")
    original = tmp_path / "old_batch.json"
    write_json(original, {"selected": [], "candidate_count": 8, "accepted": 2})
    run.state["feedback_windows"]["8"] = str(original)
    before = original.read_bytes()
    path = enqueue_regeneration(run, 8, "admission_v2_001")
    frozen = path.read_bytes()
    assert enqueue_regeneration(run, 8, "admission_v2_001") == path
    assert path.read_bytes() == frozen and original.read_bytes() == before
    assert read_json(path)["user_requested_regeneration"]
    assert len(run.state["background_jobs"]) == 1
