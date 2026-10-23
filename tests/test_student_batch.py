"""Software-only generation fixtures; real tokenizer/AppWorld checks are separate."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from recoveragent import student
from recoveragent.student import StudentSession, parse_batch
from recoveragent.teacher_history import validate_history


def block(name, arguments):
    return (
        "<tool_call>"
        + json.dumps({"name": name, "arguments": arguments})
        + "</tool_call>"
    )


def tool(name, properties=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": list(properties or {}),
            },
        },
    }


DISCOVER = tool("api_docs__show_api_doc")
LOGIN = tool("demo__login")
READ = tool("demo__read", {"access_token": {"type": "string"}})


class Tokenizer:
    chat_template = "SYNTHETIC_TEST_TEMPLATE"
    eos_token_id = 0

    def apply_chat_template(self, messages, **kwargs):
        assert kwargs["enable_thinking"] is False
        return json.dumps([messages, kwargs["tools"]])

    def __call__(self, text, **kwargs):
        return {"input_ids": [ord(c) for c in text]}

    def decode(self, ids, **kwargs):
        return "".join("<|im_end|>" if i == 0 else chr(i) for i in ids)


class Adapter:
    catalogue = [DISCOVER, LOGIN, READ]

    def __init__(self):
        self.calls = []
        self.world = SimpleNamespace(task_completed=lambda: False)

    def execute(self, name, arguments, tools):
        self.calls.append((name, json.loads(arguments)))
        if name == "api_docs__show_api_doc":
            return "public schema", [*tools, READ]
        return '{"access_token":"observed_token"}', tools


def receipt(text, finish="stop", corrupt=False):
    def generate(request):
        return {
            "policy": request["policy"],
            "finish_reason": finish,
            "input_token_ids": [] if corrupt else request["input_token_ids"],
            "token_ids": [ord(c) for c in text] + [0],
        }

    return generate


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )
    adapter = Adapter()
    return StudentSession(
        adapter,
        Tokenizer(),
        [{"role": "user", "content": "synthetic"}],
        [DISCOVER, LOGIN],
        tool_mode="dynamic_discovery",
    )


@pytest.mark.parametrize("ending", ["<|im_end|>", "<|im_end|>\n"])
def test_multiple_calls_and_tag_inside_json_string(ending):
    text = block(
        "demo__read", {"access_token": 'literal </tool_call> "quoted"'}
    ) + block("demo__login", {})
    parsed = parse_batch(text + ending, "turn1", "stop")
    assert len(parsed["tool_calls"]) == 2
    assert (
        json.loads(parsed["tool_calls"][0]["function"]["arguments"])["access_token"]
        == 'literal </tool_call> "quoted"'
    )


@pytest.mark.parametrize(
    "suffix,finish", [("<tool_call>{", "stop"), ("", "length"), ("junk", "stop")]
)
def test_rejects_whole_malformed_or_truncated_batch_before_dispatch(
    session, suffix, finish
):
    with pytest.raises(ValueError):
        session.step(receipt(block("demo__login", {}) + suffix, finish))
    assert not session.adapter.calls
    assert session.messages == [{"role": "user", "content": "synthetic"}]


def test_discovery_cannot_unlock_function_inside_same_reply(session):
    text = block("api_docs__show_api_doc", {}) + block(
        "demo__read", {"access_token": "guessed"}
    )
    with pytest.raises(ValueError, match="visible"):
        session.step(receipt(text))
    assert not session.adapter.calls


def test_dependency_uses_actual_result_on_next_generation(session):
    # Independent schema lookup and login can share a batch.
    session.step(
        receipt(block("api_docs__show_api_doc", {}) + block("demo__login", {}))
    )

    def dependent(request):
        token = json.loads(request["messages"][-1]["content"])["access_token"]
        assert token == "observed_token"
        return receipt(block("demo__read", {"access_token": token}))(request)

    session.step(dependent)
    assert session.adapter.calls[-1] == (
        "demo__read",
        {"access_token": "observed_token"},
    )
    assert [e["dispatched_count"] for e in session.events] == [2, 1]
    validate_history(session.messages)


def test_fixed_tools_and_wrong_input_or_duplicate_json_rejected(session):
    session.tool_mode = "api_predictor"
    session.step(receipt(block("api_docs__show_api_doc", {})))
    assert session.tools == [DISCOVER, LOGIN]
    with pytest.raises(ValueError, match="boundary mismatch"):
        session.step(receipt(block("demo__login", {}), corrupt=True))
    assert len(session.adapter.calls) == 1
    with pytest.raises(ValueError, match="Duplicate"):
        parse_batch(
            '<tool_call>{"name":"a","name":"b","arguments":{}}</tool_call>', "x", "stop"
        )


def test_transport_failure_keeps_unknown_generation_separate(session):
    def broken(_):
        raise TimeoutError("synthetic")

    with pytest.raises(TimeoutError):
        session.step(broken)
    assert session.termination == "infrastructure_error"
    assert session.usage["generated_tokens"] == 0
    assert session.usage["unknown_generation_reservation"] == 4096


def test_mid_batch_failure_keeps_observed_effects_and_stops(session):
    original = session.adapter.execute

    def broken(name, args, tools):
        if session.adapter.calls:
            raise OSError("synthetic")
        return original(name, args, tools)

    session.adapter.execute = broken
    with pytest.raises(OSError):
        session.step(receipt(block("demo__login", {}) + block("demo__login", {})))
    assert session.termination == "interrupted_batch"
    assert len(session.adapter.calls) == session.events[0]["dispatched_count"] == 1
    with pytest.raises(ValueError, match="terminated"):
        session.build_request()
    with pytest.raises(ValueError, match="unfinished"):
        validate_history(session.messages)


def test_injected_wall_clock_ends_session_even_if_environment_time_stops(session):
    now = [10.0]
    session.clock = lambda: now[0]
    session.started = now[0]
    session.wall_seconds = 2
    session.build_request()
    now[0] += 3
    with pytest.raises(ValueError, match="budget exhausted"):
        session.build_request()
    assert session.termination == "budget_exhausted"
    assert not session.adapter.calls
