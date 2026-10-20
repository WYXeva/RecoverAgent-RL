"""Synthetic receipts test visibility boundaries; real receipt checks are separate."""

import copy
import json

import pytest

from recoveragent.inspect_teacher import (
    Redactor,
    build_view,
    render_markdown,
    save_view,
)
from recoveragent.io import digest, files_manifest, write_json


def tool(name):
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {
                "type": "object",
                "properties": {"password": {"type": "string"}},
            },
        },
    }


def call(identity, name, arguments="{}"):
    return {
        "id": identity,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


@pytest.fixture
def receipts(tmp_path):
    directory = tmp_path / "teacher_fixture"
    initial = [
        {"role": "system", "content": "synthetic system"},
        {"role": "user", "content": "synthetic task"},
    ]
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [call("first", "demo__discover")],
    }
    feedback = {
        "role": "tool",
        "tool_call_id": "first",
        "name": "demo__discover",
        "content": '{"password":"unit-pass-quoted-\\"","access_token":"unit-token"}',
    }
    messages = initial + [assistant, feedback]
    turns = []
    for number, history, tools in (
        (1, initial, [tool("demo__discover")]),
        (2, messages, [tool("demo__discover"), tool("demo__future")]),
    ):
        request = {
            "messages": history,
            "tools": tools,
            "max_tokens": 2048,
            "model": "synthetic",
        }
        response = {
            "choices": [
                {
                    "finish_reason": "tool_calls" if number == 1 else "length",
                    "message": assistant
                    if number == 1
                    else {
                        "role": "assistant",
                        "content": 'echo unit-token unit-pass-quoted-"',
                        "tool_calls": [call("unfinished", "demo__future", '{"bad":')],
                    },
                }
            ],
            "usage": {"completion_tokens": 12 if number == 1 else 2048},
        }
        event = {
            "request_id": f"{number:03d}",
            "assistant_index": len(history),
            "history_hash": digest(history),
            "tools": tools,
            "request_hash": digest(request),
            "response_hash": digest(response),
        }
        if number == 1:
            event.update(dispatched_count=1, tool_results=[feedback])
            write_json(directory / "dispatches/001_001.json", feedback)
        else:
            event["format_error"] = "Truncated response"
        write_json(directory / f"requests/{number:03d}.request.json", request)
        write_json(directory / f"requests/{number:03d}.response.json", response)
        turns.append(event)
    write_json(
        directory / "trajectory.json",
        {
            "trajectory_id": directory.name,
            "task_id": "synthetic_task",
            "messages": messages,
            "turns": turns,
            "reward": 0,
            "status": "valid",
            "termination_reason": "invalid_completion",
        },
    )
    # An evaluator payload must never be opened or copied into a view.
    write_json(
        directory / "evaluation.json", {"secret_label": "HIDDEN_EVALUATOR_SENTINEL"}
    )
    write_json(
        tmp_path / "teacher_fixture.config.json",
        {
            "limits": {"episode_generated_tokens": 16384},
            "remaining_generated_tokens": 25000,
            "teacher": {"max_new_tokens": 2048},
        },
    )
    return directory


def test_actual_request_boundary_and_schema_are_not_rebuilt_from_final_state(receipts):
    view = build_view(receipts, turn=1)
    row = view["turns"][0]
    assert len(row["llm_input"]["messages"]) == 2
    assert [t["function"]["name"] for t in row["llm_input"]["tools"]] == [
        "demo__discover"
    ]
    assert "demo__future" not in json.dumps(row["llm_input"])
    assert row["tool_feedback_after_response"][0]["role"] == "tool"
    assert "unit-token" not in json.dumps(view)
    assert "HIDDEN_EVALUATOR_SENTINEL" not in json.dumps(view)
    assert "reward" not in row["llm_input"]
    assert view["controller_only"]["trajectory"]["reward"] == 0
    assert view["overview"][1]["tools_added"] == ["demo__future"]
    assert all(not row["receipt_warnings"] for row in view["overview"])


def test_truncated_raw_response_is_retained_without_fake_tool_results(receipts):
    view = build_view(receipts, turn=2)
    row = view["turns"][0]
    raw = row["response_receipt"]["choices"][0]
    assert raw["finish_reason"] == "length"
    assert raw["message"]["tool_calls"][0]["function"]["arguments"] == '{"bad":'
    assert row["tool_feedback_after_response"] == []
    assert row["controller_diagnostics"]["format_error"] == "Truncated response"
    assert view["overview"][1]["generated_tokens"] == 2048
    assert view["overview"][1]["known_cumulative_generated_tokens"] == 2060


def test_redaction_handles_nested_json_echoes_and_preserves_schema():
    value = {
        "tools": [tool("demo__login")],
        "content": json.dumps({"password": 'abc"\\xyz', "access_token": "unit-token"}),
        "arguments": json.dumps({"password": 'abc"\\xyz'}),
        "echo": 'abc"\\xyz and unit-token',
        "api_key": "sk-" + "syntheticnotarealkey123456",
    }
    original = copy.deepcopy(value)
    result = Redactor([value]).apply(value)
    assert value == original
    assert result["tools"] == original["tools"]
    nested = json.loads(result["content"])
    assert nested["password"].startswith("<REDACTED_")
    assert json.loads(result["arguments"])["password"] == nested["password"]
    assert nested["password"] in result["echo"]
    assert "unit-token" not in json.dumps(result)
    assert "sk-synthetic" not in json.dumps(result)


def test_missing_response_keeps_known_request_and_marks_usage_unknown(receipts):
    (receipts / "requests/002.response.json").unlink()
    view = build_view(receipts, turn=2)
    assert view["turns"][0]["response_receipt"] is None
    assert view["overview"][1]["finish_reason"] == "NO_RESPONSE_RECEIPT"
    assert view["overview"][1]["generated_tokens"] is None
    assert view["overview"][1]["known_cumulative_generated_tokens"] == 12


def test_changed_receipt_is_shown_with_warning_not_silently_corrected(receipts):
    path = receipts / "requests/001.request.json"
    request = json.loads(path.read_text())
    request["messages"][1]["content"] = "CHANGED_ACTUAL_REQUEST"
    path.write_text(json.dumps(request))
    view = build_view(receipts, turn=1)
    assert (
        view["turns"][0]["llm_input"]["messages"][1]["content"]
        == "CHANGED_ACTUAL_REQUEST"
    )
    assert "request_hash_mismatch" in view["turns"][0]["receipt_warnings"]
    assert "request_hash_mismatch" in render_markdown(build_view(receipts))
    assert (
        "recorded_history_differs_from_request" in view["turns"][0]["receipt_warnings"]
    )


def test_view_is_read_only_and_will_not_overwrite_evidence_or_output(
    receipts, tmp_path
):
    before = files_manifest(receipts)
    content = render_markdown(build_view(receipts, all_turns=True))
    with pytest.raises(ValueError):
        save_view(receipts / "new-view.md", content, receipts)
    output = tmp_path / "view.md"
    save_view(output, content, receipts)
    with pytest.raises(FileExistsError):
        save_view(output, content, receipts)
    assert files_manifest(receipts) == before
    assert "### 发出请求时：完整工具 schema" in content
    assert "HIDDEN_EVALUATOR_SENTINEL" not in content
    with pytest.raises(ValueError, match="no saved request"):
        build_view(receipts, turn=99)


def test_markdown_logs_cannot_escape_code_fence(receipts):
    view = build_view(receipts, turn=1)
    view["turns"][0]["llm_input"]["messages"][0]["content"] = "```\\n# untrusted"
    rendered = render_markdown(view)
    assert "````json" in rendered
