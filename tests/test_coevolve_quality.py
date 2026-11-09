"""Public-goal admission tests; synthetic judgments, no paid model or GPU."""

import json

import pytest

from recoveragent.coevolve_generation import (
    Evolution,
    TaskQualityError,
    novelty_result,
    require_answer_contract,
)
from recoveragent.io import read_json, write_json
from recoveragent.v2_round import config
from recoveragent.v2_tasks import plan


@pytest.mark.parametrize(
    "query",
    [
        "Create a playlist. Then tell me exactly how many songs are in it.",
        "Create a project and tell me the total number of tasks in it.",
        "Count the selected entries. Return one integer.",
    ],
)
def test_unrecognized_scalar_contract_is_deferred_before_teacher(query):
    with pytest.raises(TaskQualityError) as error:
        require_answer_contract({"instruction": query})
    assert error.value.quality_status == "deferred_contract"


@pytest.mark.parametrize(
    "query",
    [
        "Create items, then report the number of tasks created.",
        "Report the order totals in the format 'Order <id>: $<total>'.",
        "Return the ID verbatim.",
        "Return the title as a string.",
    ],
)
def test_supported_count_and_explicit_string_contracts_are_retained(query):
    require_answer_contract({"instruction": query})


@pytest.mark.parametrize(
    "change",
    [
        {"novel": "true"},
        {"goal_difference": ""},
        {"duplicate_of": "prior"},
        {"reason": None},
    ],
)
def test_malformed_or_unsubstantiated_novelty_is_rejected(change):
    value = {
        "novel": True,
        "reason": "new",
        "goal_difference": "different date",
        "duplicate_of": None,
    }
    with pytest.raises(TaskQualityError):
        novelty_result({**value, **change}, [{"id": "prior", "instruction": "old"}])


@pytest.fixture
def quality_engine(tmp_path):
    source = plan()["feedback20"][0]
    directory = tmp_path / "feedback"
    write_json(
        directory / "history.json",
        {
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {"instruction": "Return this week's order total as a string."}
                    ),
                }
            ]
        },
    )
    summary = {
        "feedback": [
            {"task_id": source, "generated_task_id": None, "directory": str(directory)}
        ],
        "teacher_usage_priced_cny": 0,
    }
    engine = Evolution(config(), {}, {}, tmp_path / "quality", summary, 1e20, None)
    engine.pool = [
        {
            "task_id": "prior",
            "source_task_id": source,
            "instruction": "Return the latest three orders' total as a string.",
        }
    ]
    task = {
        "source_task_id": source,
        "instruction": "For the three most recent orders, give their total as a string.",
        "execution_contract": {
            "expected_answer": "PRIVATE_REFERENCE_MUST_NOT_REACH_JUDGE"
        },
    }
    return engine, task, tmp_path / "candidate"


def test_semantic_duplicate_rejected_despite_different_wording(
    quality_engine, monkeypatch
):
    engine, task, output = quality_engine
    calls = []

    def judge(source, path, system, content):
        calls.append(content)
        assert content["baselines"][0]["id"].startswith("source:")
        assert content["baselines"][1]["id"] == "prior"
        assert "PRIVATE_REFERENCE" not in json.dumps(content)
        return {
            "novel": False,
            "reason": "Same latest-three-order goal, only wording changes",
            "goal_difference": "",
            "duplicate_of": "prior",
        }

    monkeypatch.setattr(engine, "text", judge)
    with pytest.raises(TaskQualityError) as error:
        engine.quality(task, output)
    assert error.value.quality_status == "duplicate_goal" and len(calls) == 1
    assert not (output / "quality.json").exists()


def test_real_condition_difference_has_hashed_public_evidence(
    quality_engine, monkeypatch
):
    engine, task, output = quality_engine
    task["instruction"] = "Return the latest five orders' total as a string."
    monkeypatch.setattr(
        engine,
        "text",
        lambda *args: {
            "novel": True,
            "reason": "Changes selected orders",
            "goal_difference": "Five instead of three orders",
            "duplicate_of": None,
        },
    )
    receipt = engine.quality(task, output)
    assert receipt == read_json(output / "quality.json")
    assert receipt["passed"] and len(receipt["source_public_history_sha256"]) == 64
    assert len(receipt["baselines_sha256"]) == len(receipt["judgment_sha256"]) == 64


def test_source_duplicate_and_unsupported_count_make_no_paid_request(
    quality_engine, monkeypatch
):
    engine, task, output = quality_engine
    monkeypatch.setattr(
        engine, "text", lambda *a: pytest.fail("Must reject before dispatch")
    )
    for instruction, status in [
        ("Return this week's order total as a string.", "duplicate_goal"),
        ("Tell me exactly how many tasks there are.", "deferred_contract"),
    ]:
        with pytest.raises(TaskQualityError) as error:
            engine.quality({**task, "instruction": instruction}, output)
        assert error.value.quality_status == status
