"""Deadline transport accounting; no real model or AppWorld execution."""

import copy
from types import SimpleNamespace

import pytest

from recoveragent import m1_vgpu_inference as runtime
from recoveragent.io import read_json


def req():
    return {
        "policy": {"checkpoint_id": "test"},
        "input_token_ids": [1, 2],
        "max_new_tokens": 4,
        "messages": [],
        "tools": [],
        "seed": 42,
    }


def deadline_receipt(request):
    return {
        **request,
        "token_ids": [3, 4],
        "finish_reason": "deadline",
        "runtime_variant": runtime.VARIANT,
    }


@pytest.mark.parametrize(
    "deadline,now,expected", [(600, 0, 600), (600, 181, 419), (600, 601, 0)]
)
def test_original_session_deadline_bounds_wait(deadline, now, expected):
    assert runtime.remaining(deadline, now) == expected


@pytest.mark.parametrize("deadline", [True, None, float("nan"), float("inf"), 601])
def test_invalid_deadline_rejected(deadline):
    with pytest.raises(ValueError):
        runtime.remaining(deadline, 0)


def fake_session(monkeypatch, tmp_path, response_factory):
    class Parent:
        def __init__(self):
            self.started, self.wall_seconds = 0, 600
            self.output = tmp_path
            self.identity = req()["policy"]
            self.usage = {
                "model_requests": 1,
                "generated_tokens": 10,
                "unknown_generation_reservation": 4,
            }
            self.termination = "infrastructure_error"

        def run(self, generate):
            return generate(req())

    monkeypatch.setattr(
        runtime.original, "engine", lambda: SimpleNamespace(StudentSession=Parent)
    )
    monkeypatch.setattr(runtime, "execution_clock", lambda: 181)

    def transport(url, wire, timeout):
        assert timeout == 424  # remaining 419 + bounded transport grace
        assert wire["deadline_monotonic"] == 600
        original = dict(wire)
        original.pop("deadline_monotonic")
        assert original == req()  # same IDs, history, budget and seed
        return response_factory(wire)

    monkeypatch.setattr(runtime, "request_json", transport)
    return runtime.deadline_engine("http://127.0.0.1:8768").StudentSession()


def test_partial_tokens_audited_counted_and_never_dispatched(monkeypatch, tmp_path):
    session = fake_session(monkeypatch, tmp_path, deadline_receipt)
    result = session.run(None)
    assert result["termination"] == "budget_exhausted"
    assert result["usage"]["generated_tokens"] == 12
    assert result["usage"]["unknown_generation_reservation"] == 0
    saved = read_json(tmp_path / "requests/001.deadline_response.json")
    assert saved["token_ids"] == [3, 4]
    assert (
        read_json(tmp_path / "generation_deadline_resolution.json")[
            "partial_tool_calls_executed"
        ]
        is False
    )


def test_normal_reply_unchanged(monkeypatch, tmp_path):
    def response(wire):
        return {**wire, "finish_reason": "eos", "token_ids": [3]}

    session = fake_session(monkeypatch, tmp_path, response)
    result = session.run(None)
    assert result["token_ids"] == [3] and result["finish_reason"] == "eos"


def test_socket_timeout_is_not_mislabeled_budget_exhaustion(monkeypatch, tmp_path):
    def failed(wire):
        raise TimeoutError("transport did not provide a deadline receipt")

    session = fake_session(monkeypatch, tmp_path, failed)
    with pytest.raises(TimeoutError):
        session.run(None)
    assert session.usage["unknown_generation_reservation"] == 4


@pytest.mark.parametrize(
    "field,value",
    [
        ("token_ids", [True]),
        ("token_ids", [1] * 5),
        ("policy", {}),
        ("input_token_ids", [9]),
        ("deadline_monotonic", 999),
        ("runtime_variant", "wrong"),
    ],
)
def test_corrupted_deadline_receipt_cannot_be_scored_as_budget_stop(field, value):
    request = dict(req(), deadline_monotonic=600)
    response = deadline_receipt(request)
    response[field] = copy.deepcopy(value)
    with pytest.raises(ValueError):
        runtime.validate_deadline_response(response, request)


def test_probe_cannot_be_enabled_by_a_status_string(monkeypatch):
    monkeypatch.setattr(runtime.probe, "verify_source", lambda: "source")
    with pytest.raises(ValueError):
        runtime.validate_probe({"status": "MICROBENCHMARK_PASS"}, "unverified")


def test_suffix_aggregate_keeps_four_prior_unknown_and_does_not_invent_rows():
    from recoveragent import m1_vgpu_resume as resume

    cfg = {"cases": [{"task_id": str(i)} for i in range(244)]}
    rows = [
        {"task_id": "46", "status": "valid", "official_success": True},
        {"task_id": "47", "status": "valid", "official_success": None},
    ]
    result = resume.summary(cfg, rows)
    assert result["attempted"] == 48
    assert result["valid"] == 43 and result["success"] == 6
    assert result["failure"] == 37 and result["unknown"] == 5
    assert result["TGC"] is None and not result["complete_valid_evaluation"]
    assert result["new_rows"] == rows
    assert not result["historical_raw_artifact_return_complete"]
    assert result["unattempted_task_ids"][0] == "48"
    with pytest.raises(ValueError):
        resume.summary(cfg, list(reversed(rows)))


def test_real_student_state_machine_never_executes_complete_call_after_deadline(
    monkeypatch, tmp_path
):
    import hashlib
    import json
    from recoveragent import student

    class Tokenizer:
        chat_template = "deadline-test-template"
        eos_token_id = 0

        def apply_chat_template(self, messages, **kwargs):
            return json.dumps(messages)

        def __call__(self, text, **kwargs):
            return {"input_ids": list(text.encode())}

        def decode(self, ids, **kwargs):
            return "".join(chr(i) for i in ids)

    tool = {
        "type": "function",
        "function": {
            "name": "demo__write",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    class Adapter:
        catalogue = [tool]
        world = SimpleNamespace(task_completed=lambda: False)
        calls = []

        def execute(self, *args):
            self.calls.append(args)
            pytest.fail("Deadline generation dispatched a tool")

    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )
    monkeypatch.setattr(
        runtime.original,
        "engine",
        lambda: SimpleNamespace(StudentSession=student.StudentSession),
    )
    monkeypatch.setattr(runtime, "execution_clock", lambda: 181)
    generated = list(b'<tool_call>{"name":"demo__write","arguments":{}}</tool_call>')

    def transport(url, wire, timeout):
        return {**deadline_receipt(wire), "token_ids": generated}

    monkeypatch.setattr(runtime, "request_json", transport)
    cls = runtime.deadline_engine("http://127.0.0.1:8768").StudentSession
    tmp_path = tmp_path / "session"
    session = cls(
        Adapter(),
        Tokenizer(),
        [{"role": "user", "content": "test"}],
        [tool],
        tool_mode="dynamic_discovery",
        output=tmp_path,
        clock=lambda: 0,
    )
    result = session.run(None)
    assert result["termination"] == "budget_exhausted"
    assert result["usage"]["generated_tokens"] == len(generated)
    assert result["usage"]["unknown_generation_reservation"] == 0
    assert not session.adapter.calls
    assert len(session.messages) == 1
    assert (
        read_json(tmp_path / "turns/001.json")["termination"] == "infrastructure_error"
    )
    assert (
        read_json(tmp_path / "generation_deadline_resolution.json")[
            "resolved_termination"
        ]
        == "budget_exhausted"
    )
