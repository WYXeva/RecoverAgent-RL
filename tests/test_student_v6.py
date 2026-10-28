"""Offline protocol regression; all generation receipts and worlds are synthetic."""

import copy
import hashlib
import json
import shutil

import pytest

from recoveragent import student
from recoveragent.answer_guard import AnswerGuard, RULES as ANSWER_RULES
from recoveragent.execution_v6 import SYSTEM_PROMPT, RUNTIME as OLD_RUNTIME
from recoveragent.harness import ProtocolHarness, REFACTOR_REVISION, RULES
from recoveragent.io import read_json
from recoveragent.student import StudentSession, V6_RUNTIME, verify_student_runtime
from recoveragent.student_evaluation import evaluation_prompt, verify_execution_runtime
from recoveragent.teacher_history import validate_history
from test_student_batch import (
    Adapter,
    DISCOVER,
    LOGIN,
    READ,
    Tokenizer,
    block,
    receipt,
    tool,
)
from test_answer_guard import TOOLS as COMPLETE_TOOLS, INSTRUCTION


DOC = tool("api_docs__show_api_descriptions", {"app_name": {"type": "string"}})
DOC["function"]["parameters"]["additionalProperties"] = False


@pytest.fixture
def make_session(monkeypatch):
    # Only the tokenizer is synthetic; runtime locks, guards and dispatch are real code.
    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )

    def make(**overrides):
        adapter = Adapter()
        adapter.catalogue = [DOC, DISCOVER, LOGIN, READ, *COMPLETE_TOOLS]
        kwargs = {
            "tool_mode": "dynamic_discovery",
            "answer_guard": True,
            "protocol_harness": True,
            "execution_protocol": V6_RUNTIME,
        }
        kwargs.update(overrides)
        messages = kwargs.pop(
            "messages",
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"instruction": INSTRUCTION})},
            ],
        )
        tools = kwargs.pop("tools", [DOC, DISCOVER, LOGIN, *COMPLETE_TOOLS])
        return StudentSession(adapter, Tokenizer(), messages, tools, **kwargs)

    return make


def test_v6_constructs_full_nonthinking_request_without_old_prompt_appendix(
    make_session, tmp_path
):
    session = make_session(output=tmp_path / "run")
    request = session.build_request()
    assert isinstance(session.harness, ProtocolHarness)
    assert session.harness.revision == REFACTOR_REVISION
    assert isinstance(session.answer_guard, AnswerGuard)
    assert session.answer_guard.contract["kind"] == "money"
    assert request["messages"] == session.messages
    assert request["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert RULES not in SYSTEM_PROMPT and ANSWER_RULES not in SYSTEM_PROMPT
    assert request["tools"] == session.tools and request["input_token_ids"]
    assert len(request["input_token_ids"]) + request["max_new_tokens"] <= 32768
    assert request["max_new_tokens"] == 4096 and request["policy"]["thinking"] is False
    assert session.memory is None
    assert read_json(
        tmp_path / "run/execution_protocol.json"
    ) == verify_student_runtime(V6_RUNTIME)


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution_protocol": None},
        {"execution_protocol": OLD_RUNTIME},
        {"protocol_harness": False},
        {"answer_guard": False},
        {"protocol_harness": False, "answer_guard": False},
        {"harness_revision": "review_20261023"},
        {"tool_mode": "api_predictor"},
        {"max_requests": 41},
        {"max_generated_tokens": 16385},
        {"max_input_tokens": 32769},
        {"max_new_tokens": 4097},
        {"wall_seconds": 601},
        {"wall_seconds": float("nan")},
    ],
)
def test_v6_label_cannot_disable_guards_or_expand_budgets(make_session, overrides):
    with pytest.raises(ValueError):
        make_session(**overrides)


def test_prompt_mutation_or_altered_public_schema_is_rejected(make_session):
    messages = make_session().messages
    messages[0]["content"] += "\n" + RULES + "\n" + ANSWER_RULES
    with pytest.raises(ValueError, match="exact prompt"):
        make_session(messages=messages)
    tools = copy.deepcopy([DOC])
    tools[0]["function"]["parameters"]["required"] = []
    with pytest.raises(ValueError, match="Non-public or altered"):
        make_session(tools=tools)


@pytest.mark.parametrize(
    "arguments,fragment",
    [
        ({}, "app_name"),
        ({"app_name": "phone", "made_up": 1}, "made_up"),
        ({"app_name": 1}, "app_name"),
    ],
)
def test_v6_precise_schema_rejects_entire_batch_and_preserves_receipt(
    make_session, tmp_path, arguments, fragment
):
    session = make_session(output=tmp_path / "run")
    text = block("demo__login", {}) + block(DOC["function"]["name"], arguments)
    event = session.step(receipt(text))
    feedback = json.loads(event["feedback"]["content"])
    assert event["status"] == "protocol_rejected" and not session.adapter.calls
    assert fragment in json.dumps(feedback["schema_error"])
    if not arguments:
        assert feedback["schema_error"]["missing_fields"] == ["app_name"]
        assert feedback["schema_error"]["path"] == []
    assert feedback["whole_batch_rejected"] and feedback["retry_allowed"]
    assert not any(m["role"] == "assistant" for m in session.messages)
    assert read_json(tmp_path / "run/requests/001.response.json")["token_ids"] == [
        ord(c) for c in text
    ] + [0]
    next_request = session.build_request()
    assert next_request["messages"][-1] == event["feedback"]
    assert next_request["messages"][0]["content"] == SYSTEM_PROMPT
    validate_history(session.messages)


def test_v6_visibility_discovery_and_actual_result_boundary(make_session):
    session = make_session()
    illegal = block("api_docs__show_api_doc", {}) + block(
        "demo__read", {"access_token": "guess"}
    )
    assert session.step(receipt(illegal))["status"] == "protocol_rejected"
    assert not session.adapter.calls
    session.step(
        receipt(block("api_docs__show_api_doc", {}) + block("demo__login", {}))
    )
    request = session.build_request()
    assert READ in request["tools"]
    token = json.loads(request["messages"][-1]["content"])["access_token"]
    assert token == "observed_token"
    session.step(receipt(block("demo__read", {"access_token": token})))
    assert session.adapter.calls[-1] == ("demo__read", {"access_token": token})
    validate_history(session.messages)


@pytest.mark.parametrize(
    "corrected,allowed", [(368, True), (369, False), ("$368", False)]
)
def test_v6_numeric_guard_keeps_single_same_value_correction(
    make_session, corrected, allowed
):
    session = make_session()
    session.step(receipt(block("supervisor__complete_task", {"answer": "$368"})))
    assert session.answer_guard.pending and not session.adapter.calls
    assert session.events[-1]["supervise"] is False
    session.step(receipt(block("supervisor__complete_task", {"answer": corrected})))
    assert session.answer_guard.correction_used
    assert session.answer_guard.correction_valid is allowed
    assert len(session.adapter.calls) == int(allowed)
    assert session.usage["model_requests"] == 2
    if allowed:
        assert session.adapter.calls[0][1] == {"answer": 368}
    else:
        assert session.stopped


def test_v6_two_protocol_corrections_and_shared_request_budget(make_session):
    session = make_session()
    invalid = receipt(block(DOC["function"]["name"], {}))
    for i in range(3):
        feedback = json.loads(session.step(invalid)["feedback"]["content"])
        assert feedback["retry_allowed"] is (i < 2)
    assert session.stopped and session.termination == "protocol_correction_exhausted"
    assert session.usage["model_requests"] == session.harness.rejections == 3
    assert not session.adapter.calls
    with pytest.raises(ValueError, match="terminated"):
        session.build_request()
    limited = make_session(max_requests=1)
    limited.step(invalid)
    with pytest.raises(ValueError, match="budget exhausted"):
        limited.step(invalid)
    assert limited.usage["model_requests"] == 1


def test_legacy_v5_still_requires_both_declarations(make_session):
    config = read_json("configs/shared_stage_validation.json")["execution"]
    messages = make_session().messages
    messages[0]["content"] = evaluation_prompt(config)
    legacy = make_session(messages=messages, execution_protocol=None)
    assert legacy.execution_protocol is None
    assert legacy.build_request()["messages"] == messages
    for missing, error in [(RULES, "harness"), (ANSWER_RULES, "answer guard")]:
        altered = copy.deepcopy(messages)
        altered[0]["content"] = altered[0]["content"].replace(missing, "")
        with pytest.raises(ValueError, match=error + " must be declared"):
            make_session(messages=altered, execution_protocol=None)


@pytest.mark.parametrize("fault", ["source", "contract", "missing_guard"])
def test_trusted_runtime_rejects_source_or_contract_drift(tmp_path, monkeypatch, fault):
    lock = read_json(student.ROOT / student.V6_LOCK)
    for name in lock["files"]:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(student.ROOT / name, destination)
    if fault == "source":
        (tmp_path / "src/recoveragent/harness.py").write_text("altered")
    elif fault == "contract":
        lock["session_contract"]["protocol_harness"] = False
    else:
        del lock["files"]["src/recoveragent/answer_guard.py"]
    path = tmp_path / student.V6_LOCK
    path.write_text(json.dumps(lock))
    monkeypatch.setattr(student, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="source changed|contract"):
        verify_student_runtime(V6_RUNTIME)


def test_both_student_entrypoints_route_to_the_new_lock(monkeypatch):
    from recoveragent import p3c

    expected = verify_student_runtime(V6_RUNTIME)
    assert verify_execution_runtime(V6_RUNTIME) == expected
    assert p3c.verify_source(V6_RUNTIME) == expected["lock_sha256"]
    config = read_json("configs/p3c_collection_20261025.json")
    config.update(
        runtime_revision=V6_RUNTIME,
        actor_prompt_variant="execution_v6",
        harness_runtime_revision=REFACTOR_REVISION,
    )

    class TaskGateReached(Exception):
        pass

    def stop_at_task_gate(path):
        assert path == config["task_manifest"]
        raise TaskGateReached

    # Validate protocol routing without reading private tasks/demonstrations.
    monkeypatch.setattr(p3c, "read_json", stop_at_task_gate)
    with pytest.raises(TaskGateReached):
        p3c.validate_config(config)
    assert evaluation_prompt(config) == SYSTEM_PROMPT
    config["actor_prompt_variant"] = "execution_v5"
    with pytest.raises(ValueError, match="v5-or-v6"):
        p3c.validate_config(config)
