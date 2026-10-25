"""Software regression fixtures; no paid Teacher or model-success claims."""

import copy
import json

import pytest

from recoveragent.demonstrations import as_demo, validate_demo, validate_receipts
from recoveragent.harness import SCHEMA_REVISION
from recoveragent.io import read_json, canonical, digest
from test_harness import collect_synthetic, session
import test_harness
from test_runner import Adapter as TeacherWorld, TOOLS
from test_teacher_data import response
from test_student_batch import block, receipt, tool

DOC = tool("api_docs__show_api_descriptions", {"app_name": {"type": "string"}})


def schema_teacher(tmp_path, monkeypatch, revision=SCHEMA_REVISION):
    config = test_harness.config()
    config["harness_runtime_revision"] = revision
    monkeypatch.setattr(test_harness, "config", lambda: copy.deepcopy(config))
    monkeypatch.setattr(TeacherWorld, "initial_tools", lambda self: [DOC, *TOOLS])
    bad = response()
    bad["choices"][0]["message"]["tool_calls"][0]["function"] = {
        "name": DOC["function"]["name"],
        "arguments": "{}",
    }
    return collect_synthetic(tmp_path, monkeypatch, [bad, response()])


def test_precise_schema_feedback_survives_demo_and_receipt_audit(tmp_path, monkeypatch):
    run, trajectory, _ = schema_teacher(tmp_path, monkeypatch)
    feedback = json.loads(trajectory["turns"][0]["feedback"]["content"])
    assert feedback["schema_error"]["missing_fields"] == ["app_name"]
    validate_demo(as_demo(trajectory))
    validate_receipts(trajectory, run / "teacher_synthetic")
    assert (
        read_json(run / "teacher_synthetic/requests/001.response.json")["choices"][0][
            "message"
        ]["tool_calls"][0]["function"]["arguments"]
        == "{}"
    )


def test_response_identity_fault_keeps_reservation_and_is_not_model_error(monkeypatch):
    s = session(monkeypatch)
    with pytest.raises(ValueError, match="identity"):
        s.step(receipt(block("demo__login", {}), corrupt=True))
    assert s.events[-1]["failure_category"] == "worker_environment"
    assert s.usage["unknown_generation_reservation"] == 4096
    assert s.usage["generated_tokens"] == 0 and not s.adapter.calls


def test_real_dispatch_count_does_not_depend_on_answer_guard(monkeypatch):
    s = session(monkeypatch)
    event = s.step(receipt(block("demo__login", {})))
    assert s.answer_guard is None
    assert event["appworld_dispatched_count"] == event["dispatched_count"] == 1


def test_teacher_identity_fault_preserves_cost_and_token_reservation(
    tmp_path, monkeypatch
):
    bad = response()
    bad["model"] = "unexpected_backend"
    run, t, _ = collect_synthetic(tmp_path, monkeypatch, [bad])
    assert (
        t["status"] == "infrastructure_error"
        and t["failure_category"] == "worker_environment"
    )
    assert (
        t["usage"]["unknown_generation_reservation"] > 0
        and t["usage"]["unknown_cost_reservation"] > 0
    )
    assert t["usage"]["generated_tokens"] == 0 and t["usage"]["usage_priced_cost"] == 0
    assert t["usage"]["provider_requests"] == 1 and t["usage"]["tool_calls"] == 0
    assert read_json(run / "teacher_synthetic/requests/001.response.json") == bad
    from recoveragent.p3a import interrupted_usage

    recovered = interrupted_usage(run / "teacher_synthetic", t["teacher"])
    assert recovered["generated_tokens"] == 0
    assert (
        recovered["unknown_generation_reservation"]
        == t["usage"]["unknown_generation_reservation"]
    )
    assert (
        recovered["unknown_cost_reservation"] == t["usage"]["unknown_cost_reservation"]
    )


def test_forged_schema_detail_cannot_pass_raw_receipt_validation(tmp_path, monkeypatch):
    run, t, _ = schema_teacher(tmp_path, monkeypatch)
    bad = copy.deepcopy(t)
    feedback = json.loads(bad["turns"][0]["feedback"]["content"])
    feedback["schema_error"]["missing_fields"] = ["invented"]
    bad["turns"][0]["feedback"]["content"] = canonical(feedback)
    bad["turns"][1]["history_hash"] = digest(
        bad["messages"][: bad["turns"][1]["assistant_index"]]
    )
    with pytest.raises(ValueError, match="Schema feedback differs"):
        validate_receipts(bad, run / "teacher_synthetic")


def test_teacher_student_schema_feedback_matches_through_real_runners(
    tmp_path, monkeypatch
):
    from recoveragent.harness import REFACTOR_REVISION

    run, t, c = schema_teacher(tmp_path, monkeypatch, REFACTOR_REVISION)
    s = session(monkeypatch, harness_revision=REFACTOR_REVISION)
    s.tools = [DOC]
    event = s.step(receipt(block(DOC["function"]["name"], {})))
    assert event["feedback"] == t["turns"][0]["feedback"]
    assert event["failure_category"] == t["turns"][0]["failure_category"] == "protocol"
    validate_demo(as_demo(t))
    validate_receipts(t, run / "teacher_synthetic")
    from recoveragent.demonstrations import export_dataset, verify_dataset

    export_dataset(run, run / "clean_dataset", {"unit_task"}, c["limits"])
    assert verify_dataset(run / "clean_dataset", {"unit_task"})
    for _ in range(2):
        s.step(receipt(block(DOC["function"]["name"], {})))
    assert s.harness.stopped and s.usage["model_requests"] == 3 and not s.adapter.calls


@pytest.mark.parametrize(
    "arguments,rule,path,fields",
    [
        ({"payload": {}}, "required", ["payload"], {"missing_fields": ["items"]}),
        (
            {"payload": {"items": [{"count": "wrong"}]}},
            "type",
            ["payload", "items", 0, "count"],
            {"expected_type": "integer"},
        ),
        (
            {"payload": {"items": [{"count": 1, "credential": "PRIVATE"}]}},
            "additionalProperties",
            ["payload", "items", 0],
            {"unknown_fields": ["credential"]},
        ),
    ],
)
def test_nested_schema_paths_and_no_secret_values(
    monkeypatch, arguments, rule, path, fields
):
    from recoveragent.harness import REFACTOR_REVISION

    s = session(monkeypatch, harness_revision=REFACTOR_REVISION)
    nested = tool(
        "demo__write",
        {
            "payload": {
                "type": "object",
                "required": ["items"],
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["count"],
                            "properties": {"count": {"type": "integer"}},
                            "additionalProperties": False,
                        },
                    }
                },
            }
        },
    )
    s.tools = [nested]
    event = s.step(receipt(block("demo__write", arguments)))
    detail = json.loads(event["feedback"]["content"])["schema_error"]
    assert detail["path"] == path and detail["rule"] == rule
    assert all(detail[k] == v for k, v in fields.items())
    assert "PRIVATE" not in event["feedback"]["content"] and not s.adapter.calls


def test_business_error_keeps_observation_and_uses_no_correction_budget(
    tmp_path, monkeypatch
):
    from recoveragent.harness import failure_category, ServiceCircuitBreaker

    result = "Execution failed. Traceback:\nResponse status code is 401"
    s = session(monkeypatch, output=tmp_path / "student")
    s.adapter.execute = lambda name, args, tools: (result, tools)
    event = s.step(receipt(block("demo__login", {})))
    assert event["failure_category"] == "business" and s.harness.rejections == 0
    assert s.messages[-1]["content"] == result and event["dispatched_count"] == 1
    assert not s.stopped and "protocol_rejection" not in event
    assert not ServiceCircuitBreaker().observe(event)
    assert failure_category({"status": "valid", "reward": 0}) == "task_failure"
    assert (
        failure_category({"termination_reason": "evaluation_error", "reward": None})
        == "evaluation_runtime"
    )
    # Tool data containing the same words is not an error envelope.
    assert (
        failure_category({"tool_results": [{"content": json.dumps({"text": result})}]})
        is None
    )
    monkeypatch.setattr(TeacherWorld, "initial_tools", lambda self: [DOC, *TOOLS])

    def execute(self, name, arguments, tools):
        self.value += 1
        self.completed = name == "supervisor__complete_task"
        return ("done" if self.completed else result), tools

    monkeypatch.setattr(TeacherWorld, "execute", execute)
    first = response()
    first["choices"][0]["message"]["tool_calls"][0]["function"] = {
        "name": DOC["function"]["name"],
        "arguments": '{"app_name":"demo"}',
    }
    last = response()
    last["choices"][0]["message"]["tool_calls"][0]["id"] = (
        "completion_after_business_error"
    )
    _, teacher, _ = collect_synthetic(tmp_path, monkeypatch, [first, last])
    assert teacher["turns"][0]["failure_category"] == "business"
    assert teacher["turns"][0]["tool_results"][0]["content"] == result
    assert (
        teacher["harness"]["rejections"] == 0
        and teacher["usage"]["provider_requests"] == 2
    )


def test_shared_recorder_retains_known_results_if_receipt_disk_write_fails(
    tmp_path, monkeypatch
):
    from recoveragent import harness

    messages = []
    event = {
        "request_id": "001",
        "inflight_call_id": "c",
        "appworld_attempted_count": 1,
    }
    call = {"id": "c", "function": {"name": "demo__write", "arguments": "{}"}}

    def fail(*a):
        raise OSError("disk unavailable")

    monkeypatch.setattr(harness, "write_json", fail)
    with pytest.raises(OSError):
        harness.record_tool_result(
            tmp_path, event, messages, call, "real write result", executed=True
        )
    assert (
        messages == event["tool_results"]
        and messages[0]["content"] == "real write result"
    )
    assert event["appworld_dispatched_count"] == event["dispatched_count"] == 1
    assert "inflight_call_id" not in event


def test_unknown_or_guard_feedback_does_not_become_official_failure():
    from recoveragent.harness import failure_category

    assert failure_category({"status": "unknown", "reward": 0}) is None
    assert (
        failure_category({"harness_verdicts": [{"error": "harness_format_error"}]})
        == "protocol"
    )


@pytest.mark.parametrize("status", ["rejected_generation", "interrupted_batch"])
def test_new_execution_fault_classifications_reach_existing_circuit(status):
    from recoveragent.harness import ServiceCircuitBreaker

    breaker = ServiceCircuitBreaker()
    assert not breaker.observe({"status": status})
    assert breaker.observe({"status": status})
    assert breaker.reason == "consecutive_service_failures"
