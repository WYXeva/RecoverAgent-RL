"""Synthetic follow-up and Responses contracts; no live models or AppWorld."""

import copy
import json
import time

import pytest

from recoveragent import p3a, teacher_followup as followup
from recoveragent.io import digest, read_json
from recoveragent.teacher import TeacherPolicy, normalize_message, response_usage
from recoveragent.teacher_responses import project_response


def raw_response():
    return {
        "id": "resp_synthetic",
        "model": "gpt-6-luna",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "id": "fc_synthetic",
                "call_id": "call_1",
                "name": "supervisor__complete_task",
                "arguments": '{"answer":"synthetic"}',
                "status": "completed",
            }
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 50},
            "output_tokens_details": {"reasoning_tokens": 5},
        },
    }


@pytest.mark.parametrize("teacher", ["deepseek", "gpt"])
def test_frozen_train_identity_clean_start_and_same_budgets(monkeypatch, teacher):
    from recoveragent.v2_tasks import plan

    monkeypatch.setattr(followup, "verify_lock", lambda: "synthetic")
    task = followup.configuration()["task_ids"][0]
    cfg = followup.worker_config(task, "synthetic", "/synthetic", teacher)
    p3a.validate_config(cfg)
    assert not cfg.get("recovery_protocol")
    assert cfg["collection_context"]["max_sequence_tokens"] == 65536
    assert cfg["remaining_requests"] == 80
    assert cfg["remaining_generated_tokens"] == 16384
    assert cfg["limits"]["episode_wall_seconds"] == 1200
    assert (
        cfg["teacher"]["base_url"]
        == followup.configuration()["teachers"]["deepseek"]["base_url"]
    )
    assert cfg["teacher"]["api_key_env"] == "DEEPSEEK_API_KEY"
    other = followup.worker_config(
        task, "synthetic", "/synthetic", "gpt" if teacher == "deepseek" else "deepseek"
    )
    assert (
        other["teacher"]["opencode_session_id"] != cfg["teacher"]["opencode_session_id"]
    )
    changed = copy.deepcopy(cfg)
    changed["teacher"]["model"] = "different"
    with pytest.raises(ValueError, match="frozen"):
        p3a.validate_config(changed)
    with pytest.raises(ValueError):
        followup.worker_config(plan()["dev24"][0], "synthetic", "/synthetic", teacher)


def test_selection_is_fixed_recovery_failure_subset_in_original_feedback_order():
    cfg = followup.configuration()
    summary = {
        "entries": [
            {"task_id": task, "acceptance": {"status": "failed"}}
            for task in reversed(cfg["task_ids"])
        ]
    }
    assert followup.select_tasks(summary) == cfg["task_ids"]
    summary["entries"][0]["acceptance"]["status"] = "eligible"
    with pytest.raises(ValueError, match="selection"):
        followup.select_tasks(summary)


@pytest.mark.parametrize("first_eligible", [True, False])
def test_fallback_once_then_restart_never_replays_paid_attempt(
    tmp_path, monkeypatch, first_eligible
):
    called = []

    def attempt(output, task, teacher, deployment):
        called.append(teacher)
        return {
            "task_id": task,
            "teacher": teacher,
            "eligible": first_eligible or teacher == "gpt",
        }

    monkeypatch.setattr(followup, "attempt", attempt)
    deployment = {"deadline_epoch": time.time() + 3600}
    task = followup.configuration()["task_ids"][0]
    result = followup.run_task(tmp_path, task, deployment)
    assert called == (["deepseek"] if first_eligible else ["deepseek", "gpt"])
    assert result["eligible"]
    assert followup.run_task(tmp_path, task, deployment) == result
    assert len(called) == (1 if first_eligible else 2)


def test_responses_two_turns_preserve_schema_arguments_observations_and_raw_usage():
    from test_runner import TOOLS

    cfg = followup.worker_config(
        followup.configuration()["task_ids"][0], "synthetic", ".", "gpt"
    )
    teacher = cfg["teacher"]
    tools = copy.deepcopy(TOOLS)
    tools[0]["function"]["parameters"]["properties"]["optional"] = {
        "type": ["boolean", "null"],
        "default": None,
    }
    messages = [
        {"role": "system", "content": "public"},
        {"role": "user", "content": "task"},
    ]
    policy = TeacherPolicy(cfg, {"DEEPSEEK_API_KEY": "synthetic"})
    request = policy.build_request(messages, tools, 4096)
    assert request["tools"][0]["strict"] is False
    assert request["tools"][0]["parameters"] == tools[0]["function"]["parameters"]
    assert policy._endpoint() == teacher["base_url"] + "/responses"
    raw = raw_response()
    response = project_response(raw)
    assert response_usage(response) == {
        "prompt_tokens": 100,
        "generated_tokens": 20,
        "cached_prompt_tokens": 50,
    }
    message = normalize_message(response)
    policy.remember_response(len(messages), response)
    messages += [
        message,
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "supervisor__complete_task",
            "content": "actual observation",
        },
    ]
    second = policy.build_request(messages, tools, 128)
    assert second["input"][2] == raw["output"][0]
    assert second["input"][3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "actual observation",
    }
    assert "previous_response_id" not in second and second["store"] is False
    bad = copy.deepcopy(response)
    bad["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{}"
    with pytest.raises(ValueError, match="projection"):
        normalize_message(bad)
    raw.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    with pytest.raises(ValueError, match="Truncated"):
        normalize_message(project_response(raw))


def test_real_teacher_loop_uses_responses_and_exports_only_assistant(
    tmp_path, monkeypatch
):
    from recoveragent import (
        APPWORLD_REVISION,
        appworld_environment,
        evaluation,
        v2_model,
    )
    from recoveragent.demonstrations import validate_demo, validate_receipts, as_demo
    from test_runner import Adapter
    from test_teacher_collection import CharacterTokenizer

    monkeypatch.setattr(followup, "verify_lock", lambda: "synthetic")
    task = followup.configuration()["task_ids"][0]
    cfg = followup.worker_config(task, "synthetic", "synthetic", "gpt")

    class ToolTokenizer(CharacterTokenizer):
        def apply_chat_template(self, messages, **kwargs):
            return "".join(
                m["content"] + json.dumps(m.get("tool_calls", [])) for m in messages
            )

    class World(Adapter):
        versions = {"revision": APPWORLD_REVISION, "data_version": "synthetic"}

        def __init__(self, *a, **kw):
            assert "recovery_source" not in kw
            super().__init__(*a, **kw)
            self.world.task.generator_id = cfg["custom_research"]["scenario_id"]

        def configure_schema_compatibility(self, revision, output):
            assert revision == "schema_nullable_compatibility_v2"

    monkeypatch.setattr(p3a, "AppWorldAdapter", World)
    monkeypatch.setattr(v2_model, "load_tokenizer", lambda _: ToolTokenizer())
    monkeypatch.setattr(evaluation, "environment_check", lambda: {"synthetic": True})
    monkeypatch.setattr(
        appworld_environment, "verify_environment", lambda _: {"synthetic": True}
    )
    monkeypatch.setattr(
        evaluation,
        "evaluate_isolated",
        lambda *a, **kw: {
            "status": "valid",
            "success": True,
            "official": {"synthetic": True},
        },
    )
    invoked = []

    def transport(request):
        assert "messages" not in request and request["input"][1]["content"]
        invoked.append(request)
        return project_response(raw_response())

    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda c, creds: TeacherPolicy(c, creds, transport=transport),
    )
    directory = tmp_path / "synthetic-attempt"
    record = p3a.run_teacher_episode(
        cfg, task, directory, {"DEEPSEEK_API_KEY": "synthetic"}
    )
    assert record["status"] == "valid", record
    assert record["reward"] == 1 and len(invoked) == 1
    assert record["start_kind"] == "original_initial" and not record.get("recovery")
    validate_demo(as_demo(record))
    validate_receipts(record, directory)
    result = followup.process_attempt(directory, cfg)
    assert result["eligible"] and result["recovery_suffix"] is False
    assert result["examples"] > 0 and result["supervised_tokens"] > 0
    assert followup.process_attempt(directory, cfg) == result
    path = directory / "requests/001.request.json"
    request = read_json(path)
    request["input"][0]["content"] = "unrecorded instructions"
    path.write_text(json.dumps(request))
    record["turns"][0]["request_hash"] = digest(request)
    with pytest.raises(ValueError, match="context mismatch"):
        validate_receipts(record, directory)


def test_new_source_lock_and_old_collection_lock_reject_changes():
    from recoveragent import teacher_collection

    assert len(followup.verify_lock()) == 64
    with pytest.raises(ValueError, match="source"):
        teacher_collection.verify_lock()
