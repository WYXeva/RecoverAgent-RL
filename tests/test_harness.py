"""Synthetic protocol/transport tests; real AppWorld evidence is reported separately."""

import copy
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from recoveragent import p3a, student, evaluation
from recoveragent.answer_guard import PROTOCOL as ANSWER_PROTOCOL
from recoveragent.demonstrations import (
    as_demo,
    export_dataset,
    verify_dataset,
    validate_receipts,
)
from recoveragent.harness import (
    PROTOCOL,
    RULES,
    ProtocolHarness,
    ProtocolViolation,
    ServiceCircuitBreaker,
    check_batch,
    provider_error_record,
)
from recoveragent.io import read_json, write_json
from recoveragent.student import StudentSession
from recoveragent.teacher import TeacherPolicy, TeacherTransportError
from test_teacher_data import config as base_config, response
from test_runner import Adapter as TeacherWorld
from test_student_batch import Adapter, Tokenizer, DISCOVER, LOGIN, READ, block, receipt


def config():
    c = base_config.__wrapped__()
    c.update(
        project_version="0.10",
        actor_prompt_variant="execution_v5",
        answer_guard_protocol=ANSWER_PROTOCOL,
        harness_protocol=PROTOCOL,
        environment_profile="appworld_sqlalchemy20_v1",
    )
    c["teacher"].update(max_new_tokens=4096, max_tool_calls_per_turn=None)
    c["limits"]["max_tool_calls"] = None
    return c


def session(monkeypatch, **kwargs):
    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )
    return StudentSession(
        Adapter(),
        Tokenizer(),
        [
            {"role": "system", "content": RULES},
            {"role": "user", "content": "synthetic"},
        ],
        [DISCOVER, LOGIN],
        tool_mode=kwargs.pop("tool_mode", "dynamic_discovery"),
        protocol_harness=True,
        **kwargs,
    )


@pytest.mark.parametrize("mode", ["dynamic_discovery", "api_predictor"])
def test_atomic_rejection_and_mode_specific_visibility(monkeypatch, mode):
    s = session(monkeypatch, tool_mode=mode)
    event = s.step(
        receipt(
            block("api_docs__show_api_doc", {})
            + block("demo__read", {"access_token": "guessed"})
        )
    )
    assert event["protocol_rejection"] == "invisible_tool"
    assert not s.adapter.calls and s.tools == [DISCOVER, LOGIN]
    assert s.messages[-1]["role"] == "user" and event["supervise"] is False
    # Discovery only works on the NEXT valid response; A remains fixed.
    s.step(receipt(block("api_docs__show_api_doc", {})))
    assert (READ in s.tools) == (mode == "dynamic_discovery")


def test_truncation_then_complete_batch_no_salvage(monkeypatch):
    s = session(monkeypatch)
    e = s.step(receipt(block("demo__login", {}) + "<tool_call>{", "length"))
    assert e["protocol_rejection"] == "truncated_output" and not s.adapter.calls
    s.step(receipt(block("demo__login", {})))
    assert len(s.adapter.calls) == 1 and s.usage["model_requests"] == 2


def test_authentication_is_checked_but_never_replaced(monkeypatch):
    s = session(monkeypatch)
    s.step(receipt(block("demo__login", {})))
    s.step(receipt(block("api_docs__show_api_doc", {})))
    e = s.step(receipt(block("demo__read", {"access_token": "altered"})))
    assert (
        e["protocol_rejection"] == "unobserved_access_token"
        and len(s.adapter.calls) == 2
    )
    assert "observed_token" not in e["feedback"]["content"]
    s.step(receipt(block("demo__read", {"access_token": "observed_token"})))
    assert s.adapter.calls[-1][1]["access_token"] == "observed_token"


def test_two_corrections_and_original_request_budget(monkeypatch):
    s = session(monkeypatch)
    for _ in range(3):
        s.step(receipt("<tool_call>{"))
    assert s.stopped and s.termination == "protocol_correction_exhausted"
    assert s.usage["model_requests"] == 3 and not s.adapter.calls
    limited = session(monkeypatch, max_requests=1)
    limited.run(receipt("<tool_call>{"))
    assert (
        limited.termination == "budget_exhausted"
        and limited.usage["model_requests"] == 1
    )


def test_context_and_output_share_one_window(monkeypatch):
    s = session(monkeypatch)
    n = len(s.build_request()["input_token_ids"])
    s.max_input_tokens = n + 15
    assert s.build_request()["max_new_tokens"] == 15
    s.max_input_tokens = n
    with pytest.raises(ValueError, match="context"):
        s.build_request()


def test_truncation_at_total_generation_cap_does_not_get_extra_tokens(monkeypatch):
    s = session(monkeypatch, max_generated_tokens=8)

    def consume(request):
        return {
            "policy": request["policy"],
            "input_token_ids": request["input_token_ids"],
            "finish_reason": "length",
            "token_ids": [42] * request["max_new_tokens"],
        }

    s.run(consume)
    assert s.termination == "budget_exhausted"
    assert s.usage["generated_tokens"] == 8 and s.usage["model_requests"] == 1
    assert not s.adapter.calls


def test_receipt_identity_error_is_not_model_correction(monkeypatch):
    s = session(monkeypatch)
    with pytest.raises(ValueError, match="identity"):
        s.step(receipt(block("demo__login", {}), corrupt=True))
    assert (
        not s.messages[-1]["content"].startswith('{"source"')
        and s.harness.rejections == 0
    )


def test_duplicate_json_and_completion_order_fail_closed():
    m = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "a",
                "type": "function",
                "function": {
                    "name": "demo__read",
                    "arguments": '{"access_token":"a","access_token":"b"}',
                },
            }
        ],
    }
    with pytest.raises(ProtocolViolation, match="invalid_arguments"):
        check_batch(m, [], [READ])


def test_notices_require_matching_queries_and_are_bounded():
    def history(q1, q2):
        messages = []
        for i, q in enumerate([q1, q2]):
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": str(i),
                                "function": {
                                    "name": "demo__list",
                                    "arguments": json.dumps(
                                        {"query": q, "page_index": i}
                                    ),
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": str(i), "content": "[]"},
                ]
            )
        return messages

    h = ProtocolHarness("dynamic_discovery")
    assert h.notice(history("A", "B")) is None
    assert h.notice(history("A", "A")) is not None
    assert h.notice(history("A", "A")) is not None
    assert h.notice(history("A", "A")) is None


def test_numeric_correction_cannot_acquire_protocol_retries():
    from recoveragent.answer_guard import AnswerGuard

    guard = AnswerGuard("What is the total cost?")
    guard.inspect(
        [
            {
                "function": {
                    "name": "supervisor__complete_task",
                    "arguments": '{"answer":"$12"}',
                }
            }
        ]
    )
    h = ProtocolHarness("dynamic_discovery")
    feedback = h.reject("malformed_output", answer_pending=guard.pending)
    guard.inspect([])
    assert h.stopped and guard.stopped
    assert not json.loads(feedback["content"])["retry_allowed"]


def test_evaluator_profile_observes_errors_without_changing_tracker(monkeypatch):
    class Tracker:
        def __init__(self):
            self.failures = []
            self.success = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type:
                self.failures.append(exc_type.__name__)
                self.success = False
            return True

        def to_dict(self):
            return {"failures": self.failures}

    def evaluate(*args, **kwargs):
        t = Tracker()
        with t:
            raise AssertionError("hidden assertion contents")
        with t:
            raise RuntimeError("hidden runtime contents")
        return t

    original = Tracker.__exit__
    monkeypatch.setitem(
        sys.modules,
        "appworld.evaluator",
        SimpleNamespace(TestTracker=Tracker, evaluate_task=evaluate),
    )
    previous = sys.getprofile()
    result = evaluation.evaluate_here("synthetic", "synthetic")
    assert Tracker.__exit__ is original and sys.getprofile() is previous
    assert result["official"]["failures"] == ["AssertionError", "RuntimeError"]
    assert result["exception_types"] == ["RuntimeError"] and result["success"] is None
    assert "hidden" not in json.dumps(result)


@pytest.mark.parametrize("status", [401, 402, 403])
def test_global_provider_errors_open_circuit_without_exposing_body(status):
    b = ServiceCircuitBreaker()
    safe = provider_error_record(
        TeacherTransportError(f"Teacher HTTP {status}; usage unknown")
    )
    assert b.observe({"provider_error": safe})
    assert safe["http_status"] == status and not safe["usage_known"]
    assert "secret" not in json.dumps(
        provider_error_record(TeacherTransportError("secret"))
    )


def test_transient_circuit_reset_and_appworld_401_is_not_provider_error():
    b = ServiceCircuitBreaker()
    transient = {
        "provider_error": provider_error_record(TeacherTransportError("timeout"))
    }
    assert not b.observe(transient)
    assert not b.observe({"status": "valid", "tool_result": "401"})
    assert not b.observe(transient)
    assert b.observe(transient)


def test_controller_stops_and_preserves_unattempted_manifest(tmp_path, monkeypatch):
    c = config()
    monkeypatch.setitem(
        sys.modules,
        "appworld",
        SimpleNamespace(
            load_task_ids=lambda _: ["task1", "task2"], update_root=lambda _: None
        ),
    )
    monkeypatch.setattr(evaluation, "environment_check", lambda: {"synthetic": True})
    monkeypatch.setattr(
        p3a, "read_credentials", lambda _: {"DEEPSEEK_API_KEY": "synthetic"}
    )
    launched = []

    def worker(command, **kwargs):
        out = command[command.index("--output") + 1]
        task = command[command.index("--task") + 1]
        launched.append(task)
        usage = p3a.interrupted_usage(tmp_path / "empty", c["teacher"])
        usage.update(
            provider_requests=1,
            unknown_generation_reservation=4096,
            unknown_cost_reservation=1,
        )
        write_json(
            out + "/trajectory.json",
            {
                "trajectory_id": out.rsplit("/", 1)[-1],
                "task_id": task,
                "status": "infrastructure_error",
                "usage": usage,
                "provider_error": provider_error_record(
                    TeacherTransportError("Teacher HTTP 402; usage unknown")
                ),
            },
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(p3a.subprocess, "run", worker)
    result = p3a.collect(c, tmp_path / "run")
    assert launched == ["task1"] and result["attempts"] == 1
    assert result["service_stop_reason"] == "provider_global_block"
    assert result["unattempted_schedule"] == [("task2", "dynamic_discovery")]
    assert result["costs"]["unknown_cost_reservation"] == 1


def collect_synthetic(tmp_path, monkeypatch, outputs):
    from recoveragent import APPWORLD_REVISION

    c = config()

    class World(TeacherWorld):
        versions = {"revision": APPWORLD_REVISION, "data_version": "synthetic"}

    monkeypatch.setattr(p3a, "AppWorldAdapter", World)
    monkeypatch.setattr(evaluation, "environment_check", lambda: {"synthetic": True})
    monkeypatch.setattr(
        evaluation,
        "evaluate_isolated",
        lambda *a: {
            "status": "valid",
            "success": True,
            "official": {"synthetic": True},
        },
    )
    replies = iter(outputs)
    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda c, creds: TeacherPolicy(c, creds, transport=lambda _: next(replies)),
    )
    run = tmp_path / "run"
    write_json(run / "config.json", c)
    t = p3a.run_teacher_episode(
        c, "unit_task", run / "teacher_synthetic", {"DEEPSEEK_API_KEY": "synthetic"}
    )
    return run, t, c


@pytest.mark.parametrize("kind", ["invisible", "length", "malformed", "duplicate_id"])
def test_teacher_rejection_export_receipts_and_mask_targets(
    tmp_path, monkeypatch, kind
):
    bad, good = response(), response()
    if kind == "invisible":
        bad["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = (
            "demo__hidden"
        )
    elif kind == "length":
        bad["choices"][0]["finish_reason"] = "length"
    elif kind == "malformed":
        bad["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{"
    else:
        bad["choices"][0]["message"]["tool_calls"] *= 2
    run, t, c = collect_synthetic(tmp_path, monkeypatch, [bad, good])
    assert t["status"] == "valid", t
    assert t["usage"]["provider_requests"] == 2 and t["usage"]["tool_calls"] == 1
    assert t["turns"][0]["supervise"] is False
    from recoveragent.inspect_teacher import build_view

    view = build_view(run / "teacher_synthetic", all_turns=True)
    assert view["overview"][0]["recorded_tool_results"] == 0
    assert view["overview"][0]["harness_feedback_messages"] == 1
    assert view["turns"][0]["tool_feedback_after_response"] == [
        t["turns"][0]["feedback"]
    ]
    validate_receipts(t, run / "teacher_synthetic")
    dataset = run / "clean_dataset"
    assert (
        export_dataset(run, dataset, {"unit_task"}, c["limits"])["counts"]["accepted"]
        == 1
    )
    verify_dataset(dataset, {"unit_task"})
    demo = as_demo(t)
    assert len([x for x in demo["turns"] if x["supervise"]]) == 1
    assert [m["role"] for m in demo["messages"]] == [
        "system",
        "user",
        "user",
        "assistant",
        "tool",
    ]
    tampered = copy.deepcopy(t)
    tampered["turns"][0]["protocol_rejection"] = "invalid_arguments"
    with pytest.raises(ValueError):
        validate_receipts(tampered, run / "teacher_synthetic")


def test_provider_error_persists_unknown_reservation(tmp_path, monkeypatch):
    c = config()
    monkeypatch.setattr(evaluation, "environment_check", lambda: {})
    monkeypatch.setattr(p3a, "AppWorldAdapter", TeacherWorld)

    def fail(_):
        raise TeacherTransportError("Teacher HTTP 402; usage unknown")

    monkeypatch.setattr(
        p3a, "TeacherPolicy", lambda c, creds: TeacherPolicy(c, creds, transport=fail)
    )
    t = p3a.run_teacher_episode(
        c, "unit_task", tmp_path / "teacher_error", {"DEEPSEEK_API_KEY": "synthetic"}
    )
    assert t["provider_error"]["http_status"] == 402
    assert t["usage"]["unknown_generation_reservation"] == 4096
    assert t["usage"]["unknown_cost_reservation"] > 0
    assert (
        read_json(tmp_path / "teacher_error/trajectory.json")["provider_error"]
        == t["provider_error"]
    )


def test_notice_is_visible_auditable_and_does_not_change_tools(tmp_path, monkeypatch):
    from test_runner import TOOLS

    monkeypatch.setattr(TeacherWorld, "initial_tools", lambda self: [*TOOLS, READ])

    def execute(self, name, arguments, tools):
        if name == "supervisor__complete_task":
            self.completed = True
        return "[]", tools

    monkeypatch.setattr(TeacherWorld, "execute", execute)
    replies = []
    for i in range(2):
        r = response()
        c = r["choices"][0]["message"]["tool_calls"][0]
        c["id"] = f"read_{i}"
        c["function"] = {"name": "demo__read", "arguments": '{"access_token":"token"}'}
        replies.append(r)
    replies.append(response())
    run, t, c = collect_synthetic(tmp_path, monkeypatch, replies)
    assert t["status"] == "valid" and t["usage"]["harness_notices"] == 1
    validate_receipts(t, run / "teacher_synthetic")
    assert t["turns"][1]["harness_notice"] in t["messages"]
    assert t["turns"][2]["tools"] == t["turns"][0]["tools"]
    assert (
        export_dataset(run, run / "clean_dataset", {"unit_task"}, c["limits"])[
            "counts"
        ]["accepted"]
        == 1
    )


def test_evaluation_runtime_error_does_not_become_task_failure(tmp_path, monkeypatch):
    run, t, _ = collect_synthetic(tmp_path, monkeypatch, [response()])
    assert t["status"] == "valid"
    monkeypatch.setattr(
        evaluation,
        "evaluate_isolated",
        lambda *a: {
            "status": "evaluation_error",
            "success": None,
            "exception_types": ["OperationalError"],
        },
    )
    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda c, creds: TeacherPolicy(c, creds, transport=lambda _: response()),
    )
    t = p3a.run_teacher_episode(
        config(),
        "unit_task",
        run / "teacher_evaluation_error",
        {"DEEPSEEK_API_KEY": "synthetic"},
    )
    assert t["status"] == "infrastructure_error" and t["reward"] is None
    assert t["termination_reason"] == "evaluation_error"
    assert (run / "teacher_evaluation_error/trajectory.json").exists()
