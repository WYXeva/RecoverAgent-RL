"""Real request/context code; only Teacher transport and the CPU world are fake."""

import copy
import json
import time
from pathlib import Path

import pytest

from recoveragent import p3a, teacher_context, v2_teacher
from recoveragent.coevolve_generation import ContextTooLong
from recoveragent.io import read_json
from recoveragent.s2_evolution import GroupEvolution
from recoveragent.s2_experiment import configuration
from recoveragent.teacher import TeacherPolicy, TeacherBlocked
from recoveragent.v2_runtime import (
    S2_RUNTIME,
    S2_TEACHER_CONTEXT_EXECUTION,
)


@pytest.fixture
def scoped(monkeypatch):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_TEACHER_CONTEXT_EXECUTION)
    # Pin the real public tokenizer artifact; no Student tokenizer or fake counter.
    local = Path("runs/s2_context_validation_20261117/teacher_tokenizer")
    if (local / "tokenizer.json").exists():
        monkeypatch.setenv("RECOVERAGENT_TEACHER_TOKENIZER_DIR", str(local.resolve()))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-only")
    return configuration()


def text_engine(cfg, tmp_path):
    return GroupEvolution(
        cfg,
        cfg["models"][cfg["selected_model"]],
        {"appworld_root": "synthetic", "teacher_budget_amount": 10},
        tmp_path,
        {"teacher_usage_priced_usd": 0},
        time.monotonic() + 1800,
        None,
        budget_root=tmp_path,
    )


def install_transport(monkeypatch):
    seen = []

    def post(self, payload):
        seen.append(copy.deepcopy(payload))
        return {
            "model": self.config["model"],
            "id": "synthetic",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"tasks": []}'},
                }
            ],
            "usage": {"prompt_tokens": 71000, "completion_tokens": 10},
        }

    monkeypatch.setattr(TeacherPolicy, "_post_session", post)
    return seen


def test_real_group_and_parent_allow_teacher_above_student_window(
    scoped, tmp_path, monkeypatch
):
    seen = install_transport(monkeypatch)
    e = text_engine(scoped, tmp_path)
    assert e.raw_text(
        scoped["feedback_task_ids"][0],
        tmp_path / "text",
        "Output JSON",
        {"evidence": " a" * 70000},
    ) == {"tasks": []}
    assert len(seen) == 1
    receipt = read_json(tmp_path / "text/context.json")
    assert 65536 < receipt["input_tokens_estimate"] < 1000000
    assert receipt["within_budget"] and not receipt["provider_exact"]
    assert receipt["reserved_output_tokens"] == 4096
    assert receipt["tokenizer"]["repo"].startswith("deepseek-ai/")
    assert read_json(tmp_path / "text/usage.json")["prompt_tokens"] == 71000
    assert len(seen[0]["messages"][1]["content"]) > 140000


def test_actual_parent_rejects_teacher_overflow_before_request_or_reservation(
    scoped, tmp_path, monkeypatch
):
    seen = install_transport(monkeypatch)
    e = text_engine(scoped, tmp_path)
    with pytest.raises(ContextTooLong):
        e.raw_text(
            scoped["feedback_task_ids"][0],
            tmp_path / "text",
            "Output JSON",
            {"evidence": " a" * 1000000},
        )
    assert seen == [] and not (tmp_path / "text/s2_operation.json").exists()
    assert not read_json(tmp_path / "text/context.json")["within_budget"]


def test_input_includes_reasoning_tools_and_output_reserve(scoped):
    cfg = v2_teacher.configuration(
        scoped["feedback_task_ids"][0],
        scoped["models"][scoped["selected_model"]],
        "synthetic",
        10,
        recovery=False,
        runtime=S2_RUNTIME,
        purpose="s2_exploration",
    )
    policy = TeacherPolicy(cfg, {"DEEPSEEK_API_KEY": "synthetic"})
    messages = [{"role": "user", "content": "x"}]
    request = policy.build_request(messages, [], 4096)
    settings = cfg["teacher_context"]
    before = teacher_context.receipt(request, settings)
    request["messages"].append(
        {"role": "assistant", "content": "x", "reasoning_content": " a" * 70000}
    )
    request["tools"] = [
        {
            "type": "function",
            "function": {"name": "query", "description": " evidence" * 2000},
        }
    ]
    after = teacher_context.receipt(request, settings)
    assert after["input_tokens_estimate"] > before["input_tokens_estimate"] + 70000
    narrow = {**settings, "max_sequence_tokens": after["input_tokens_estimate"] + 4095}
    assert not teacher_context.receipt(request, narrow)["within_budget"]
    assert scoped["teacher"]["billing_context_tokens"] == 1048576
    assert scoped["teacher"]["max_request_bytes"] == 4194304
    with pytest.raises(TeacherBlocked, match="REQUEST_SIZE_LIMIT"):
        policy.build_text_request([{"role": "user", "content": "x" * 4194304}], 4096)


def test_scope_leaves_ordinary_teacher_and_student_64k(scoped):
    from recoveragent.teacher_collection import context_receipt
    from test_teacher_collection import CharacterTokenizer

    args = (
        scoped["feedback_task_ids"][0],
        scoped["models"][scoped["selected_model"]],
        "synthetic",
        10,
    )
    ordinary = v2_teacher.configuration(*args, recovery=False, runtime=S2_RUNTIME)
    recovery = v2_teacher.configuration(*args, runtime=S2_RUNTIME)
    assert "teacher_context" not in ordinary and "teacher_context" not in recovery
    assert ordinary["learning_context"]["max_sequence_tokens"] == 65536
    assert scoped["student_limits"]["max_input_tokens"] == 65536
    result = context_receipt(
        CharacterTokenizer(),
        [{"role": "user", "content": "x" * 65536}],
        [],
        1,
        ordinary["learning_context"],
        ordinary["student"],
    )
    assert not result["within_budget"]


@pytest.mark.parametrize("over", [False, True])
def test_optional_exploration_real_p3a_loop_uses_teacher_window(
    scoped, tmp_path, monkeypatch, over
):
    from recoveragent import APPWORLD_REVISION, evaluation, appworld_environment
    from recoveragent.v2_tasks import context_for
    from test_runner import Adapter
    from test_teacher_data import response

    task = scoped["feedback_task_ids"][0]
    wc = v2_teacher.configuration(
        task,
        scoped["models"][scoped["selected_model"]],
        "synthetic",
        10,
        recovery=False,
        runtime=S2_RUNTIME,
        purpose="s2_exploration",
    )
    v2_teacher.validate_config(wc)
    payload = {
        "protocol": "coevolve_appworld_v1",
        "mode": "exploration",
        "scoring_revision": "coevolve_llm_reward_v1",
        "context": {"evidence": " a" * (1000000 if over else 70000)},
    }

    class World(Adapter):
        versions = {"revision": APPWORLD_REVISION, "data_version": "synthetic"}

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.world.task.generator_id = context_for(task)["scenario_id"]
            from types import SimpleNamespace

            self.world.requester = SimpleNamespace(request=self.request)
            self.instruction = "synthetic"
            row = SimpleNamespace(status=None, instruction=self.instruction)
            row.save = lambda: setattr(self, "instruction", row.instruction)
            self.world.models = {
                "supervisor": {"Task": SimpleNamespace(find_all=lambda: [row])}
            }

        def request(self, app, api, **kwargs):
            if api == "update_task":
                self.instruction = kwargs["instruction"]
            return {
                "instruction": self.instruction,
                "status": "success" if self.completed else "incomplete",
                "answer": "synthetic",
            }

        def public_task(self):
            return {"instruction": self.instruction}

        def configure_schema_compatibility(self, *a):
            pass

    seen = []
    reply = response()
    reply.update(model=wc["teacher"]["model"], id="synthetic")

    def post(self, request):
        seen.append(request)
        return copy.deepcopy(reply)

    monkeypatch.setattr(TeacherPolicy, "_post_session", post)
    monkeypatch.setattr(p3a, "AppWorldAdapter", World)
    monkeypatch.setattr(evaluation, "environment_check", lambda: {"synthetic": True})
    monkeypatch.setattr(
        appworld_environment, "verify_environment", lambda _: {"synthetic": True}
    )
    monkeypatch.setattr(
        appworld_environment, "require_usable_execution", lambda *a: None
    )
    result = p3a.run_teacher_episode(
        wc,
        task,
        tmp_path / "episode",
        {"DEEPSEEK_API_KEY": "synthetic"},
        exploration=payload,
    )
    audit = read_json(tmp_path / "episode/context/001.json")
    assert audit["max_sequence_tokens"] == 1000000
    assert audit["input_tokens_estimate"] > 65536
    assert audit["within_budget"] is not over
    assert len(seen) == int(not over)
    assert result["termination_reason"] == (
        "context_budget_exhausted" if over else "completion_claim"
    )
    if not over:
        assert json.dumps(payload["context"])[20:120] in json.dumps(seen[0])
