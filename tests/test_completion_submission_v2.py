"""Synthetic receipts, real parser/Schema/AnswerGuard/Student dispatch chain."""

import copy
import json

import pytest

from recoveragent.answer_guard import AnswerGuard, COMPLETE
from recoveragent.completion_contract import REVISION, NOTICE, render
from recoveragent.harness import ProtocolHarness, check_batch
from recoveragent.v2_model import parse_native, _parameter, ParserContractError
from recoveragent.teacher_answer_format import public_task
from test_native_nullable import ANSWER
from test_student_batch import receipt
from test_student_v6 import make_session as session_fixture


@pytest.fixture
def make_session(monkeypatch, current_v6_unit_lock):
    return session_fixture.__wrapped__(monkeypatch, current_v6_unit_lock)


def tools(schema=ANSWER):
    return [
        {
            "type": "function",
            "function": {
                "name": COMPLETE,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": schema,
                        "status": {"type": "string", "enum": ["success", "fail"]},
                    },
                    "required": ["status"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def xml(answer=None, *, include=True, status="success"):
    return (
        "<tool_call><function=supervisor__complete_task>"
        + (f"<parameter=answer>{answer}</parameter>" if include else "")
        + f"<parameter=status>{status}</parameter></function></tool_call>"
    )


def run_session(make_session, instruction, schema=ANSWER):
    from recoveragent.execution_v6 import SYSTEM_PROMPT

    ts = tools(schema)
    # Synthetic Adapter catalogue only. The production Schema/check/dispatch code is used.
    s = make_session()
    s.adapter.catalogue = copy.deepcopy(ts)
    s.tools = copy.deepcopy(ts)
    s.messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                public_task(
                    {"instruction": instruction},
                    {"completion_contract_revision": REVISION},
                )
            ),
        },
    ]
    s.answer_guard = AnswerGuard.from_public_task(json.loads(s.messages[1]["content"]))
    s.model_contract = {"tool_format": "qwen_xml"}
    return s


def test_numeric_complete_chain_first_dispatch(make_session):
    s = run_session(make_session, "How many unread threads are in my outbox?")
    event = s.step(receipt(xml("12.5")))
    assert s.adapter.calls[-1] == (COMPLETE, {"answer": 12.5, "status": "success"})
    assert event["appworld_dispatched_count"] == 1
    assert s.answer_guard.first_submission_valid is True


def test_multivalue_json_first_dispatch_and_teacher_agree(make_session):
    contract = {
        "required": True,
        "encoding": "json_string",
        "schema": {
            "type": "object",
            "required": ["cost", "matches"],
            "additionalProperties": False,
            "properties": {"cost": {"type": "number"}, "matches": {"type": "boolean"}},
        },
    }
    instruction = render("Report cart cost and quantities match.", contract)
    text = '{"cost":12.5,"matches":true}'
    s = run_session(make_session, instruction)
    event = s.step(receipt(xml(text)))
    assert event["appworld_dispatched_count"] == 1 and not s.answer_guard.pending
    assert s.adapter.calls[-1][1]["answer"] == text
    teacher = AnswerGuard.from_public_task(json.loads(s.messages[1]["content"]))
    calls = [
        {
            "id": "t",
            "function": {
                "name": COMPLETE,
                "arguments": json.dumps({"status": "success", "answer": text}),
            },
        }
    ]
    assert teacher.inspect(calls) == [None]
    check_batch({"role": "assistant", "tool_calls": calls}, s.messages[:2], s.tools)


@pytest.mark.parametrize(
    "answer,include", [("null", True), (None, False), ('"bad"', True)]
)
def test_required_answer_correction_is_bounded(make_session, answer, include):
    s = run_session(make_session, "How many unread threads are in my outbox?")
    first = s.step(receipt(xml(answer, include=include)))
    assert not s.adapter.calls and first["harness_verdicts"][0]["retry_allowed"]
    s.step(receipt(xml(answer, include=include)))
    assert s.stopped and not s.adapter.calls and s.answer_guard.correction_used


@pytest.mark.parametrize(
    "instruction",
    [
        "Send the note.",
        render(
            "Send the note.",
            {
                "required": False,
                "encoding": "value",
                "schema": {"type": ["string", "null"]},
            },
        ),
    ],
)
def test_action_null_permitted_without_keyword_guess(make_session, instruction):
    s = run_session(make_session, instruction)
    s.step(receipt(xml("null")))
    assert s.adapter.calls[-1][1]["answer"] is None


def test_status_fail_does_not_force_an_answer(make_session):
    s = run_session(make_session, "How many unread threads are in my outbox?")
    s.step(receipt(xml(include=False, status="fail")))
    assert s.adapter.calls[-1][1] == {"status": "fail"}


def test_schema_then_numeric_share_one_correction(make_session):
    s = run_session(make_session, "How many unread threads are in my outbox?")
    first = s.step(receipt(xml("12", status="not_a_status")))
    assert first["completion_rejection"] and not s.adapter.calls
    s.step(receipt(xml("$12")))
    assert s.stopped and not s.adapter.calls


def test_malformed_completion_bounded_but_business_budget_unchanged(make_session):
    s = run_session(make_session, "Send the note.")
    malformed = xml("abc")[:-12]
    s.step(receipt(malformed))
    s.step(receipt(malformed))
    assert s.stopped and not s.adapter.calls
    h = ProtocolHarness("dynamic_discovery")
    g = AnswerGuard("Send the note.", revision=REVISION)
    for _ in range(2):
        h.rejection_event(ValueError("other malformed call"), truncated=False, guard=g)
    assert not h.stopped and not g.pending


def test_proven_interface_conflict_invalid_not_model_retry(make_session):
    s = run_session(
        make_session, "How many unread threads are in my outbox?", {"type": "string"}
    )
    event = s.step(receipt(xml("12")))
    assert s.stopped and event["reward_valid"] is False
    assert s.reward_fault["origin"] == "parser_harness"
    assert not s.adapter.calls and not s.answer_guard.pending


@pytest.mark.parametrize(
    "raw,expected",
    [("007", "007"), ("Alice", "Alice"), ('{"x":2}', '{"x":2}'), ("[2]", "[2]")],
)
def test_string_semantics_without_scalar_contract(raw, expected):
    assert _parameter(raw, ANSWER) == expected


def test_real_object_array_types_and_nonfinite_not_coerced():
    assert _parameter('{"x":2}', {"type": "object"}) == {"x": 2}
    assert _parameter("[2]", {"type": "array"}) == [2]
    contract = {"required": True, "encoding": "value", "schema": {"type": "number"}}
    assert _parameter("1e2", ANSWER, answer_contract=contract) == 100
    for raw in ("NaN", "Infinity", "1e999", "007"):
        value = _parameter(raw, ANSWER, answer_contract=contract)
        assert isinstance(value, str)


def test_history_not_rewritten_and_explicit_schema_preserved():
    source = {"instruction": "Return code 007 exactly.", "other": "original"}
    original = copy.deepcopy(source)
    enriched = public_task(source, {"completion_contract_revision": REVISION})
    assert source == original and enriched["instruction"] == original["instruction"]
    assert enriched["completion_contract"] == NOTICE
    guard = AnswerGuard.from_public_task(enriched)
    assert guard.output_contract is None


def test_illegal_batch_never_executes_legal_prefix(make_session):
    s = run_session(make_session, "How many unread threads are in my outbox?")
    # Two completions violate whole-batch ordering, even though the first is valid.
    s.step(receipt(xml("12") + xml("13")))
    assert not s.adapter.calls


def test_parser_contract_error_is_not_inferred_from_repetition():
    with pytest.raises(ParserContractError):
        parse_native(
            xml("12"),
            "x",
            "stop",
            tools({"type": "string"}),
            {"tool_format": "qwen_xml"},
            answer_contract={
                "required": True,
                "encoding": "value",
                "schema": {"type": "number"},
            },
        )


def test_demonstrated_parser_corruption_ends_invalid(make_session, monkeypatch):
    from recoveragent import v2_model

    original = v2_model._parameter

    def broken(text, schema, **kwargs):
        value = original(text, schema, **kwargs)
        return (
            str(value)
            if kwargs.get("answer_contract") and isinstance(value, (int, float))
            else value
        )

    monkeypatch.setattr(v2_model, "_parameter", broken)
    s = run_session(make_session, "How many unread threads are in my outbox?")
    event = s.step(receipt(xml("12")))
    assert event["reward_valid"] is False and not s.adapter.calls and s.stopped
    assert "Parser changed" in s.reward_fault["reason"]


def test_explicit_string_name_null_is_text():
    contract = {"required": True, "encoding": "value", "schema": {"type": "string"}}
    assert _parameter("null", ANSWER, answer_contract=contract) == "null"
