"""Regression tests for the review patch; all model/world fixtures are synthetic."""

import copy
import json
from types import SimpleNamespace

import pytest

from recoveragent import p3a, evaluation
from recoveragent.harness import (
    ProtocolHarness,
    ProtocolViolation,
    ServiceCircuitBreaker,
    check_batch,
)
from recoveragent.io import digest, read_json, write_json
from recoveragent.demonstrations import (
    validate_receipts,
    export_dataset,
    verify_dataset,
)
from recoveragent.teacher import TeacherPolicy
from test_harness import config, collect_synthetic, session
from test_runner import Adapter as TeacherWorld, TOOLS
from test_teacher_data import response
from test_student_batch import READ, LOGIN, block, receipt


def plain():
    r = response()
    r["choices"][0] = {
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": "I cannot finish this task."},
    }
    return r


def call(name, args, identity="c"):
    return {
        "id": identity,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def batch(*calls):
    return {"role": "assistant", "content": "", "tool_calls": list(calls)}


def test_teacher_plain_stop_feedback_export_and_original_receipt(tmp_path, monkeypatch):
    raw = plain()
    run, t, c = collect_synthetic(tmp_path, monkeypatch, [raw, response()])
    assert t["status"] == "valid" and t["usage"]["provider_requests"] == 2
    assert t["turns"][0]["protocol_rejection"] == "unexpected_stop"
    assert t["turns"][0]["supervise"] is False
    assert read_json(run / "teacher_synthetic/requests/001.response.json") == raw
    second = read_json(run / "teacher_synthetic/requests/002.request.json")
    assert second["messages"][-1] == t["turns"][0]["feedback"]
    assert "status='fail'" in second["messages"][-1]["content"]
    validate_receipts(t, run / "teacher_synthetic")
    export_dataset(run, run / "clean_dataset", {"unit_task"}, c["limits"])
    assert verify_dataset(run / "clean_dataset", {"unit_task"})


def test_student_same_stop_feedback_and_bounded_replies(monkeypatch):
    s = session(monkeypatch)
    for _ in range(3):
        e = s.step(receipt("I cannot finish this task."))
        assert e["protocol_rejection"] == "unexpected_stop"
        assert not s.adapter.calls
    assert s.stopped and s.termination == "protocol_correction_exhausted"
    assert s.usage["model_requests"] == 3


def test_teacher_student_stop_feedback_is_identical(tmp_path, monkeypatch):
    _, t, _ = collect_synthetic(tmp_path, monkeypatch, [plain(), response()])
    s = session(monkeypatch)
    e = s.step(receipt("I cannot finish this task."))
    assert e["feedback"] == t["turns"][0]["feedback"]


def test_already_completed_student_does_not_generate(monkeypatch):
    s = session(monkeypatch)
    s.adapter.world.task_completed = lambda: True

    def forbidden(_):
        pytest.fail("Already completed tasks must not regenerate")

    result = s.run(forbidden)
    assert result["termination"] == "completion_claim"
    assert s.usage["model_requests"] == 0 and not s.adapter.calls


def test_already_completed_teacher_does_not_generate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        TeacherWorld,
        "initial_tools",
        lambda self: setattr(self, "completed", True) or TOOLS,
    )
    _, t, _ = collect_synthetic(tmp_path, monkeypatch, [])
    assert t["usage"]["provider_requests"] == 0
    assert t["termination_reason"] == "completion_claim"


def test_explicit_failure_completion_is_not_intercepted(tmp_path, monkeypatch):
    complete = copy.deepcopy(TOOLS[0])
    complete["function"]["parameters"]["properties"]["status"] = {
        "enum": ["success", "fail"]
    }
    monkeypatch.setattr(TeacherWorld, "initial_tools", lambda self: [complete])
    fail = response()
    fail["choices"][0]["message"]["tool_calls"] = [
        call("supervisor__complete_task", {"status": "fail"})
    ]
    run, t, _ = collect_synthetic(tmp_path, monkeypatch, [fail])
    assert t["harness"]["rejections"] == 0 and t["usage"]["tool_calls"] == 1
    validate_receipts(t, run / "teacher_synthetic")
    s = session(monkeypatch)
    s.tools.append(complete)
    s.step(receipt(block("supervisor__complete_task", {"status": "fail"})))
    assert s.harness.rejections == 0
    assert s.adapter.calls[-1][1] == {"status": "fail"}


def test_plain_reply_cannot_extend_numeric_correction(monkeypatch):
    from recoveragent.answer_guard import AnswerGuard

    s = session(monkeypatch)
    s.answer_guard = AnswerGuard("What is the total cost?")
    s.answer_guard.inspect([call("supervisor__complete_task", {"answer": "$12"})])
    s.step(receipt("I am thinking."))
    assert s.stopped and s.answer_guard.stopped
    assert s.usage["model_requests"] == 1


def test_ordinary_environment_errors_count_and_reset():
    b = ServiceCircuitBreaker()
    error = {
        "status": "infrastructure_error",
        "termination_reason": "infrastructure_error",
    }
    assert not b.observe(error)
    assert not b.observe(
        {
            "status": "valid",
            "reward": 0,
            "termination_reason": "protocol_correction_exhausted",
        }
    )
    assert not b.observe(error)
    assert b.observe(error) and b.reason == "consecutive_service_failures"


@pytest.mark.parametrize("status", [401, 402, 403, None])
def test_controller_stops_global_errors_or_two_initialization_failures(
    tmp_path, monkeypatch, status
):
    import sys

    c = config()
    c["collection_profile"] = "scenario_expansion_v1"
    c["continue_after_infrastructure_error"] = True
    c["limits"]["max_tasks"] = 4
    tasks = ["task1", "task2", "task3", "task4"]
    monkeypatch.setattr(p3a, "validate_config", lambda _: None)
    monkeypatch.setattr(
        p3a, "read_credentials", lambda _: {"DEEPSEEK_API_KEY": "synthetic"}
    )
    monkeypatch.setattr(evaluation, "environment_check", lambda: {})
    from recoveragent import sampling, protocol_lock

    monkeypatch.setattr(sampling, "load_plan", lambda _: {"tasks": tasks})
    monkeypatch.setattr(protocol_lock, "verify_lock", lambda _: "synthetic")
    c["protocol_lock"] = str(tmp_path / "lock.json")
    write_json(c["protocol_lock"], {})
    monkeypatch.setitem(
        sys.modules,
        "appworld",
        SimpleNamespace(load_task_ids=lambda _: tasks, update_root=lambda _: None),
    )
    launched = []

    def worker(command, **kwargs):
        out = command[command.index("--output") + 1]
        task = command[command.index("--task") + 1]
        launched.append(task)
        t = {
            "trajectory_id": out.rsplit("/", 1)[-1],
            "task_id": task,
            "status": "infrastructure_error",
            "termination_reason": "infrastructure_error",
            "usage": p3a.interrupted_usage(tmp_path / "empty", c["teacher"]),
        }
        if status:
            t["provider_error"] = {"http_status": status}
        write_json(out + "/trajectory.json", t)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(p3a.subprocess, "run", worker)
    result = p3a.collect(c, tmp_path / "run")
    n = 1 if status else 2
    assert launched == tasks[:n]
    assert len(result["unattempted_schedule"]) == 4 - n
    assert result["service_stop_reason"] == (
        "provider_global_block" if status else "consecutive_service_failures"
    )


@pytest.mark.parametrize("actor", ["teacher", "student"])
def test_partial_batch_side_effects_and_unknown_result_are_preserved(
    tmp_path, monkeypatch, actor
):
    if actor == "teacher":
        monkeypatch.setattr(TeacherWorld, "initial_tools", lambda _: [*TOOLS, LOGIN])

        def execute(self, *args):
            self.value += 1
            if self.value == 2:
                raise OSError("synthetic write committed before connection loss")
            return '{"access_token":"token"}', args[-1]

        monkeypatch.setattr(TeacherWorld, "execute", execute)
        r = response()
        r["choices"][0]["message"] = batch(
            call("demo__login", {}, "a"), call("demo__login", {}, "b")
        )
        run, t, c = collect_synthetic(tmp_path, monkeypatch, [r])
        out = run / "teacher_synthetic"
        e = t["turns"][0]
        assert t["termination_reason"] == "interrupted_batch" and t["reward"] is None
        assert t["interruption"]["status"] == "saved"
        assert read_json(out / "interruption/dbs/db.json") == 2
        write_json(run / "teacher_synthetic.config.json", c)
        recovered = p3a.interrupted_usage(out, c["teacher"])
        assert recovered["tool_calls"] == 2 and recovered["tool_results_missing"] == 1
    else:
        s = session(monkeypatch, output=tmp_path / "student")
        s.adapter.value = 0

        def execute(*args):
            s.adapter.value += 1
            if s.adapter.value == 2:
                raise OSError("synthetic write committed before connection loss")
            return '{"access_token":"token"}', args[-1]

        s.adapter.execute = execute
        s.adapter.export_state = lambda identity, dest: write_json(
            dest / "db.json", s.adapter.value
        )
        s.adapter.runtime = lambda: {"synthetic": True}
        s.adapter.fingerprint = lambda: digest(s.adapter.value)
        with pytest.raises(OSError):
            s.step(receipt(block("demo__login", {}) * 2))
        out, e = s.output, s.events[0]
        assert e["interruption"]["status"] == "saved"
        assert read_json(out / "interruption/dbs/db.json") == 2
    assert e["dispatched_count"] == 1 and e["appworld_attempted_count"] == 2
    assert len(e["tool_results"]) == 1 and e["inflight_call_id"]
    manifest = read_json(out / "interruption/manifest.json")
    assert manifest["logical_state_hash"] == digest(2)
    assert manifest["resume_eligible"] is False
    assert manifest["uncertain_call_id"] == e["inflight_call_id"]
    assert len(list((out / "call_intents").glob("*.json"))) == 2
    assert len(list((out / "dispatches").glob("*.json"))) == 1


def test_mixed_parameter_batch_is_rejected_before_any_call(monkeypatch):
    s = session(monkeypatch)
    e = s.step(
        receipt(block("demo__login", {}) + block("demo__login", {"unexpected": True}))
    )
    assert e["protocol_rejection"] == "invalid_arguments" and not s.adapter.calls


def test_token_evidence_is_prior_same_app_and_multiple_logins_allowed():
    def history(name, token):
        return [
            batch(call(name, {}, name)),
            {
                "role": "tool",
                "tool_call_id": name,
                "name": name,
                "content": json.dumps({"access_token": token}),
            },
        ]

    # Another application's token must not constrain this application.
    other = history("other__login", "other")
    check_batch(batch(call("demo__read", {"access_token": "external"})), other, [READ])
    prior = history("demo__login", "first")
    for token in ["first"]:
        check_batch(batch(call("demo__read", {"access_token": token})), prior, [READ])
    newer = history("demo__login", "second")
    newer[0]["tool_calls"][0]["id"] = newer[1]["tool_call_id"] = "login2"
    for token in ["first", "second"]:
        check_batch(
            batch(call("demo__read", {"access_token": token})), prior + newer, [READ]
        )
    with pytest.raises(ProtocolViolation, match="unobserved_access_token"):
        check_batch(batch(call("demo__read", {"access_token": "typo"})), prior, [READ])


def test_notices_never_stop_and_query_changes_break_empty_page_match():
    h = ProtocolHarness("dynamic_discovery")

    def messages(a, b, result="[]"):
        return [
            batch(call("demo__list", a, "a")),
            {"role": "tool", "tool_call_id": "a", "content": result},
            batch(call("demo__list", b, "b")),
            {"role": "tool", "tool_call_id": "b", "content": result},
        ]

    for key in ["query", "filter", "sort_by", "page_limit", "access_token"]:
        assert (
            h.notice(messages({key: "a", "page_index": 0}, {key: "b", "page_index": 1}))
            is None
        )
    for _ in range(2):
        assert h.notice(messages({"page_index": 0}, {"page_index": 1}))
    assert h.notice(messages({}, {}, "Response status code is 401")) is None
    assert not h.stopped and h.rejections == 0
    assert ProtocolHarness("dynamic_discovery").notice(
        messages({}, {}, "same business observation")
    )


def test_evaluation_worker_crash_is_separate_from_policy_failure(tmp_path, monkeypatch):
    run, _, _ = collect_synthetic(tmp_path, monkeypatch, [response()])

    def broken(*a):
        raise OSError("synthetic evaluator process failure")

    monkeypatch.setattr(evaluation, "evaluate_isolated", broken)
    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda c, creds: TeacherPolicy(c, creds, transport=lambda _: response()),
    )
    t = p3a.run_teacher_episode(
        config(),
        "unit_task",
        run / "teacher_eval_error",
        {"DEEPSEEK_API_KEY": "synthetic"},
    )
    assert t["termination_reason"] == "evaluation_error" and t["reward"] is None
    assert t["failure_category"] == "evaluation_runtime"
    b = ServiceCircuitBreaker()
    assert b.observe(t) and b.reason == "evaluation_runtime_error"


def test_teacher_mixed_parameter_batch_never_executes_prefix(tmp_path, monkeypatch):
    invalid = response()
    monkeypatch.setattr(TeacherWorld, "initial_tools", lambda _: [*TOOLS, LOGIN])
    invalid["choices"][0]["message"] = batch(
        call("demo__login", {}, "a"), call("demo__login", {"illegal": True}, "b")
    )
    run, t, _ = collect_synthetic(tmp_path, monkeypatch, [invalid, response()])
    assert t["turns"][0]["protocol_rejection"] == "invalid_arguments"
    assert t["turns"][0]["appworld_dispatched_count"] == 0
    assert t["usage"]["tool_calls"] == 1
    validate_receipts(t, run / "teacher_synthetic")


def test_real_tool_401_text_is_model_context_not_provider_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(TeacherWorld, "initial_tools", lambda _: [*TOOLS, READ])

    def execute(self, name, args, tools):
        if name == "supervisor__complete_task":
            self.completed = True
            return "done", tools
        return "Execution failed. Response status code is 401. Unauthorized.", tools

    monkeypatch.setattr(TeacherWorld, "execute", execute)
    replies = []
    for i in range(2):
        r = response()
        r["choices"][0]["message"] = batch(
            call("demo__read", {"access_token": "public_literal"}, str(i))
        )
        replies.append(r)
    run, t, _ = collect_synthetic(tmp_path, monkeypatch, replies + [response()])
    assert t["usage"]["provider_requests"] == 3 and t["usage"]["tool_calls"] == 3
    assert "provider_error" not in t
    assert (
        json.loads(t["turns"][1]["harness_notice"]["content"])["notice"]
        == "repeated_authentication_failure"
    )
    assert not ServiceCircuitBreaker().observe(t)
    assert (
        "401"
        in read_json(run / "teacher_synthetic/requests/003.request.json")["messages"][
            -2
        ]["content"]
    )
    validate_receipts(t, run / "teacher_synthetic")


def test_failed_snapshot_is_explicit_and_never_masks_execution_error(
    monkeypatch, tmp_path
):
    s = session(monkeypatch, output=tmp_path / "student")

    def broken(*a):
        raise OSError("original execution failure")

    def failed_save(*a):
        raise RuntimeError("snapshot unavailable")

    s.adapter.execute = broken
    s.adapter.export_state = failed_save
    with pytest.raises(OSError, match="original execution failure"):
        s.step(receipt(block("demo__login", {})))
    assert s.events[-1]["interruption"]["status"] == "save_error"
    assert s.events[-1]["interruption"]["error_type"] == "RuntimeError"
    assert s.events[-1]["error_type"] == "OSError"


def test_interrupted_turn_json_does_not_erase_unknown_provider_usage(tmp_path):
    c = config()
    out = tmp_path / "teacher_partial"
    write_json(tmp_path / "teacher_partial.config.json", c)
    write_json(
        out / "requests/001.reservation.json", {"max_cost": 1.25, "max_tokens": 4096}
    )
    (out / "turns").mkdir()
    (out / "turns/001.json").write_text('{"event":')
    usage = p3a.interrupted_usage(out, c["teacher"])
    assert usage["unknown_cost_reservation"] == 1.25
    assert usage["unknown_generation_reservation"] == 4096
    assert (
        usage["unreadable_turn_records"] == 1 and usage["harness_counters_lower_bound"]
    )
