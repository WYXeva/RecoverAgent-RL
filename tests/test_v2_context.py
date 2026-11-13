"""64k software boundaries only; no model weights, GPU or paid requests."""

import copy
import hashlib
import json

import pytest

from recoveragent import base_sft, student, v2_data, v2_training
from recoveragent.coevolve_generation import Evolution
from recoveragent.execution_v6 import SYSTEM_PROMPT
from recoveragent.io import digest, read_json, write_json
from recoveragent.schema_compatibility import SCHEMA_REVISION
from recoveragent.v2_context import sequence_limit, validate_request_context
from recoveragent.v2_grpo import segments
from recoveragent.v2_round import config
from recoveragent.v2_tasks import context_for, plan
from recoveragent.v2_runtime import S1_RUNTIME
from recoveragent.v2_teacher import configuration, validate_config
import test_nullable_recovery as fixtures
from test_student_batch import Tokenizer
from test_teacher_collection import CharacterTokenizer

worlds = fixtures.worlds


def test_current_window_and_other_budgets_are_consistent(current_v2_unit_lock):
    cfg = config()
    previous = read_json("configs/v2_round1_coevolve_goals_20261110.json")
    assert cfg["student_limits"] == {
        **previous["student_limits"],
        "max_input_tokens": 65536,
    }
    assert sequence_limit(cfg) == 65536
    task = plan()["feedback20"][0]
    for recovery in (True, False):
        teacher = configuration(
            task, cfg["models"]["primary"], "synthetic", 2, recovery=recovery
        )
        validate_config(teacher)
        assert teacher["learning_context"]["max_sequence_tokens"] == 65536
        teacher["learning_context"]["max_sequence_tokens"] = 32768
        with pytest.raises(ValueError, match="configuration changed"):
            validate_config(teacher)
    for key in ("lora", "base_sft", "recovery_sft", "teacher", "grpo"):
        assert cfg[key] == previous[key]


@pytest.mark.parametrize("recovery", [False, True])
def test_complete_64k_sft_preserves_targets_and_rejects_overflow(monkeypatch, recovery):
    monkeypatch.setattr(base_sft, "validate_demo", lambda r: r)
    task = plan()["feedback20"][0]
    messages = [
        {"role": "system", "content": "x" * 65533},
        {"role": "user", "content": "y"},
        {"role": "assistant", "content": "ok"},
    ]
    sample = {
        "task_id": task,
        "scenario_id": task.rsplit("_", 1)[0],
        "trajectory_id": "synthetic",
        "messages": messages,
        "turns": [
            {"assistant_index": 2, "tools": [], "history_hash": digest(messages[:2])}
        ],
    }
    if recovery:
        # The inherited prefix remains context-only, even at the 64k boundary.
        sample["recovery"] = {"suffix_start": 2}
    before = copy.deepcopy(sample)
    rows = v2_data.encode(sample, CharacterTokenizer())
    assert len(rows[0]["input_ids"]) == 65536
    assert rows[0]["labels"] == [-100] * 65534 + [ord("o"), ord("k")]
    assert sum(rows[0]["loss_mask"]) == 2 and sample == before
    v2_training.validate_training_rows(rows, sequence_limit())
    with pytest.raises(ValueError, match="length"):
        v2_training.validate_training_rows(rows, 32768)
    damaged = copy.deepcopy(rows)
    damaged[0]["labels"][0] = 1
    with pytest.raises(ValueError, match="mask"):
        v2_training.validate_training_rows(damaged, 65536)
    messages[0]["content"] += "x"
    with pytest.raises(ValueError, match="too long"):
        v2_data.encode(sample, CharacterTokenizer())
    rows[0]["input_ids"].append(1)
    with pytest.raises(ValueError, match="length"):
        v2_training.validate_training_rows(rows, 65536)


def test_student_window_is_bound_to_lock_and_backend(worlds, tmp_path, monkeypatch):
    class SizedTokenizer(Tokenizer):
        count = 61440

        def __call__(self, *a, **kw):
            return {"input_ids": [1] * self.count}

    task = plan()["feedback20"][0]
    adapter = worlds(task, context_for(task))
    adapter.configure_schema_compatibility(SCHEMA_REVISION, tmp_path / "schema")
    tokenizer = SizedTokenizer()
    model = {
        **config()["models"]["primary"],
        "chat_template_sha256": hashlib.sha256(
            tokenizer.chat_template.encode()
        ).hexdigest(),
    }
    monkeypatch.setattr("recoveragent.v2_model.validate_identity", lambda x: x)
    kwargs = dict(
        tool_mode="dynamic_discovery",
        protocol_harness=True,
        answer_guard=True,
        execution_protocol=S1_RUNTIME,
        model_contract=model,
        **config()["student_limits"],
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": '{"instruction":"synthetic"}'},
    ]
    session = student.StudentSession(
        adapter, tokenizer, messages, adapter.initial_tools(), **kwargs
    )
    request = session.build_request()
    assert len(request["input_token_ids"]) + request["max_new_tokens"] == 65536
    assert request["max_new_tokens"] == 4096
    validate_request_context(request, 65536)
    with pytest.raises(ValueError, match="mismatch"):
        validate_request_context(request, 32768)
    from recoveragent.v2_inference import generate

    with pytest.raises(ValueError, match="mismatch"):
        generate(
            None, None, request, identity=model, deadline=1e20, max_input_tokens=32768
        )
    request["input_token_ids"].append(1)
    with pytest.raises(ValueError, match="exceeds context"):
        validate_request_context(request, 65536)
    tokenizer.count = 65536
    with pytest.raises(ValueError, match="too long"):
        session.build_request()
    assert session.termination == "context_budget_exhausted"
    with pytest.raises(ValueError, match="budget exceeds"):
        student.StudentSession(
            adapter,
            tokenizer,
            messages,
            adapter.initial_tools(),
            **{**kwargs, "max_input_tokens": 65537},
        )
    # Legacy harness sessions retain their old bound, even with a larger argument.
    with pytest.raises(ValueError, match="cannot exceed 32768"):
        student.StudentSession(
            adapter,
            tokenizer,
            messages,
            adapter.initial_tools(),
            **{**kwargs, "model_contract": None},
        )


def test_grpo_preserves_long_segment_and_rejects_changed_or_missing_window(tmp_path):
    request = {
        "policy": {"checkpoint": "current"},
        "input_token_ids": [1] * 65534,
        "max_input_tokens": 65536,
        "max_new_tokens": 2,
    }
    response = {
        "policy": request["policy"],
        "input_token_ids": request["input_token_ids"],
        "token_ids": [2, 3],
        "logprobs": [-1.0, -2.0],
    }
    root = tmp_path / "session/requests"
    write_json(root / "001.request.json", request)
    write_json(root / "001.response.json", response)
    rows = segments(tmp_path, request["policy"])
    assert rows[0]["completion_ids"] == [2, 3] and len(rows[0]["prompt_ids"]) == 65534
    for limit in (32768, None):
        bad = {**request, "max_input_tokens": limit}
        if limit is None:
            bad.pop("max_input_tokens")
        (root / "001.request.json").write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="mismatch"):
            segments(tmp_path, request["policy"])
    (root / "001.request.json").write_text(json.dumps(request))
    response["token_ids"].append(4)
    response["logprobs"].append(-3.0)
    (root / "001.response.json").write_text(json.dumps(response))
    with pytest.raises(ValueError, match="exceeds context"):
        segments(tmp_path, request["policy"])


def test_coevolve_text_overflow_never_calls_provider(tmp_path, monkeypatch):
    from recoveragent import coevolve_generation as module

    monkeypatch.setattr(module, "read_credentials", lambda _: {})

    class ForbiddenPolicy:
        def __init__(self, *a):
            pass

        def build_text_request(self, messages, reserve):
            return {"messages": messages, "max_tokens": reserve}

        def transport(self, request):
            pytest.fail("Oversized request reached the paid provider")

    monkeypatch.setattr(module, "TeacherPolicy", ForbiddenPolicy)
    summary = {"teacher_usage_priced_cny": 0.0}
    cfg = config()
    engine = Evolution(
        cfg,
        cfg["models"]["primary"],
        {"teacher_budget_cny": 2, "appworld_root": "synthetic"},
        tmp_path,
        summary,
        1e20,
        None,
    )
    engine.context_tokenizer = CharacterTokenizer()
    with pytest.raises(ValueError, match="context too long"):
        engine.text(plan()["feedback20"][0], tmp_path / "text", "x" * 61440, {})
    assert not read_json(tmp_path / "text/context.json")["within_budget"]
    assert summary["teacher_usage_priced_cny"] == 0
