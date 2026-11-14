"""v2 contracts on synthetic worlds; no paid model calls or benchmark tasks."""

import copy
import hashlib
import json
from collections import Counter
from types import SimpleNamespace

import pytest

from recoveragent import state, student, p3c
from recoveragent.adapter import call_code
from recoveragent.io import read_json, write_json
from recoveragent.v2_tasks import plan, context_for, RUNTIME
from recoveragent.v2_runtime import S1_RUNTIME
from recoveragent.v2_model import parse_native, NativeTokenizer
from recoveragent.v2_data import mix_recovery
from recoveragent.v2_grpo import group_advantages, segments
from recoveragent.v2_generator import accept, evaluate, candidate
from recoveragent.recovery_suffix import restore_teacher_start
from recoveragent.schema_compatibility import SCHEMA_REVISION
from recoveragent.execution_v6 import SYSTEM_PROMPT
import test_nullable_recovery as fixtures
from test_student_batch import Tokenizer, receipt, block

worlds = fixtures.worlds


def test_scenario_split_and_fixed_lists():
    frozen = plan()
    assert Counter(r["role"] for r in frozen["rows"]) == {
        "train": 510,
        "dev": 111,
        "test": 111,
    }
    for scenario in {r["scenario_id"] for r in frozen["rows"]}:
        assert (
            len({r["role"] for r in frozen["rows"] if r["scenario_id"] == scenario})
            == 1
        )
    assert len(set(frozen["feedback20"])) == 20
    assert len(set(frozen["dev24"])) == 24
    with pytest.raises(ValueError):
        context_for(frozen["dev24"][0], training=True)


@pytest.fixture
def native_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "gmail__synthetic",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "read": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
                        "text": {"type": "string"},
                        "count": {"type": "integer", "minimum": 1, "maximum": 5},
                        "kind": {"type": "string", "enum": ["one", "two"]},
                    },
                    "required": ["text", "count", "kind"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def native(arguments):
    return (
        "<tool_call>\n<function=gmail__synthetic>\n"
        + "".join(f"<parameter={k}>\n{v}\n</parameter>\n" for k, v in arguments.items())
        + "</function>\n</tool_call><|im_end|>"
    )


@pytest.mark.parametrize(
    "damage", [None, "null_nonnullable", "missing", "type", "bounds", "enum"]
)
def test_native_parser_preserves_strict_harness(native_tools, damage):
    values = {"read": "None", "text": "  spaced text  ", "count": "2", "kind": "one"}
    if damage == "null_nonnullable":
        values["count"] = "null"
    elif damage == "missing":
        del values["count"]
    elif damage == "type":
        values["read"] = "wrong"
    elif damage == "bounds":
        values["count"] = "6"
    elif damage == "enum":
        values["kind"] = "three"
    message = parse_native(
        native(values), "id", "eos", native_tools, {"tool_format": "qwen_xml"}
    )
    fn = message["tool_calls"][0]["function"]
    if damage:
        with pytest.raises(Exception):
            call_code(fn["name"], fn["arguments"], native_tools)
    else:
        _, args = call_code(fn["name"], fn["arguments"], native_tools)
        assert args["read"] is None and args["text"] == values["text"]


def test_native_rendering_does_not_mutate_structured_messages():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "x", "arguments": '{"read":null,"text":"None"}'}}
            ],
        }
    ]
    original = copy.deepcopy(messages)
    t = NativeTokenizer(SimpleNamespace(apply_chat_template=lambda x, **k: x))
    rendered = t.apply_chat_template(messages)
    assert rendered[0]["tool_calls"][0]["function"]["arguments"] == {
        "read": None,
        "text": "None",
    }
    assert messages == original


def test_v2_snapshot_takeover_dynamic_tools_and_atomic_batch(
    worlds, tmp_path, monkeypatch
):
    task = plan()["feedback20"][0]
    adapter = worlds(task, context_for(task))
    adapter.configure_schema_compatibility(SCHEMA_REVISION, tmp_path / "schemas")
    model = {
        "model": "synthetic",
        "revision": "synthetic",
        "thinking": False,
        "chat_template_sha256": hashlib.sha256(
            Tokenizer.chat_template.encode()
        ).hexdigest(),
        "tool_format": "qwen_json",
    }
    monkeypatch.setattr("recoveragent.v2_model.validate_identity", lambda x: x)
    contract = read_json("configs/v2_learning_64k_20261112_lock.json")[
        "session_contract"
    ]
    monkeypatch.setattr(
        student, "verify_student_runtime", lambda x: {"session_contract": contract}
    )
    session = student.StudentSession(
        adapter,
        Tokenizer(),
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": '{"instruction":"synthetic"}'},
        ],
        adapter.initial_tools(),
        tool_mode="dynamic_discovery",
        protocol_harness=True,
        answer_guard=True,
        execution_protocol=RUNTIME,
        model_contract=model,
        max_input_tokens=65536,
        output=tmp_path / "session",
    )
    session.runtime_revision = RUNTIME
    session.step(
        receipt(
            block(
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            )
        )
    )
    adapter.value = 73
    source = tmp_path / "source"
    p3c.save_boundary(session, source, "post_action", {})
    assert state.inspect_snapshot(source)[0]["format_version"] == 6
    restored = worlds(task, context_for(task))
    messages, tools, _ = restore_teacher_start(
        restored, source, tmp_path / "teacher", {}
    )
    assert (
        restored.value == 73
        and messages[:-1] == session.messages
        and tools == session.tools
    )
    call_code(
        "gmail__show_category_sizes", {"read": None, "access_token": "token"}, tools
    )
    assert session.build_request()["remaining_wall_seconds"] <= 600
    bad = worlds(task, context_for(task))
    bad.configure_schema_compatibility(SCHEMA_REVISION, tmp_path / "bad")
    bad.versions["schema_compatibility"]["compatible_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        state.restore(bad, source)
    assert bad.imports == 0


def test_recovery_mix_weights_by_supervised_tokens_and_rejects_dev():
    task = plan()["feedback20"][0]
    a = [{"task_id": task, "loss_mask": [0, 1, 1]}]
    b = [{"task_id": task, "loss_mask": [0, 1]}] * 3
    rows = mix_recovery(a, b)
    assert sum(
        r["example_weight"] for r in rows if r["mix_kind"] == "recovery"
    ) == pytest.approx(
        sum(r["example_weight"] for r in rows if r["mix_kind"] == "normal")
    )
    with pytest.raises(ValueError):
        mix_recovery([{**a[0], "task_id": plan()["dev24"][0]}], b)


def group():
    return [
        {
            "task_id": "one",
            "generated_task_id": None,
            "initial_state_hash": "same",
            "policy": {"checkpoint": "S1"},
            "classification": "natural_failure",
            "success": bool(i % 2),
        }
        for i in range(4)
    ]


def test_grpo_groups_current_policy_same_initial_and_unknown_not_zero():
    rows = group()
    advantages = group_advantages(rows)
    assert advantages[0] < 0 < advantages[1] and sum(advantages) == 0
    for key in ["task_id", "initial_state_hash", "policy"]:
        bad = copy.deepcopy(rows)
        bad[0][key] = "different"
        with pytest.raises(ValueError):
            group_advantages(bad)
    rows[0]["classification"] = "infrastructure_error"
    assert group_advantages(rows) is None
    assert (
        group_advantages(
            [{**r, "classification": "natural_failure", "success": False} for r in rows]
        )
        == [0.0] * 4
    )


def test_grpo_uses_each_actual_dynamic_request_and_only_generated_tokens(tmp_path):
    root = tmp_path / "session/requests"
    policy = {"checkpoint": "S1"}
    for i, prompt in enumerate([[1, 2], [8, 9, 10]]):
        write_json(
            root / f"{i}.request.json",
            {
                "policy": policy,
                "input_token_ids": prompt,
                "max_input_tokens": 65536,
                "max_new_tokens": 4096,
            },
        )
        write_json(
            root / f"{i}.response.json",
            {
                "policy": policy,
                "input_token_ids": prompt,
                "token_ids": [3, 4],
                "logprobs": [-1.0, -2.0],
            },
        )
    result = segments(tmp_path, policy)
    assert result[1]["prompt_ids"] == [8, 9, 10]
    assert result[0]["completion_ids"] == result[1]["completion_ids"] == [3, 4]
    with pytest.raises(ValueError):
        segments(tmp_path, {"checkpoint": "stale_S0"})


def test_generator_real_proposal_required_and_negative_evaluator(monkeypatch):
    task_id = plan()["feedback20"][0]
    proposal = {
        "read": False,
        "categories": ["inbox", "outbox"],
        "rationale": "Student omitted the second category",
    }
    messages = [
        {"role": "system", "content": "synthetic"},
        {"role": "user", "content": '{"instruction":"different original goal"}'},
    ]
    for i, (name, args, result) in enumerate(
        [
            ("gmail__show_category_sizes", {"read": False}, {"inbox": 2, "outbox": 3}),
            (
                "supervisor__complete_task",
                {"answer": json.dumps(proposal)},
                {"message": "complete"},
            ),
        ]
    ):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": str(i),
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": str(i), "content": json.dumps(result)},
            ]
        )
    task = candidate(
        {"task_id": task_id, "messages": messages},
        {"weakness": "coverage"},
        "synthetic",
    )
    assert task["expected_answer"] == 5 and task["source_context"]["role"] == "train"
    current = {"instruction": task["instruction"], "status": None, "answer": None}
    adapter = SimpleNamespace(
        world=SimpleNamespace(
            requester=SimpleNamespace(request=lambda *a, **k: current)
        )
    )
    adapter.generated_business_sha256 = "initial"
    monkeypatch.setattr(
        "recoveragent.v2_generator.business_hash",
        lambda _: current.get("business", "initial"),
    )
    assert not evaluate(adapter, task)["success"]
    current.update(status="success", answer=5)
    assert evaluate(adapter, task)["success"]
    current["business"] = "unexpected email mutation"
    assert not evaluate(adapter, task)["success"]
    current.pop("business")
    current["answer"] = 6
    assert not evaluate(adapter, task)["success"]
    checks = [
        {
            "mode": m,
            "initial_state_hash": "same",
            "evaluation": {"success": m == "positive"},
        }
        for m in ["positive", "noop", "omit_completion", "wrong_answer"]
    ]
    assert accept(task, checks)["validation"]["passed"]
    checks[1]["evaluation"]["success"] = True
    with pytest.raises(ValueError):
        accept(task, checks)


def test_new_lock_and_immutable_historical_lock_rejection():
    from recoveragent.teacher_collection import verify_lock as previous_collection
    from recoveragent.teacher_followup import verify_lock as followup_lock
    from recoveragent.teacher_followup import RETRY
    from recoveragent.v2_runtime import S1_RUNTIME, verify_lock
    from recoveragent.b10_pilot import verify_lock as old

    assert len(followup_lock(RETRY)) == 64
    with pytest.raises(ValueError):
        followup_lock()
    with pytest.raises(ValueError):
        verify_lock(S1_RUNTIME)
    with pytest.raises(ValueError):
        verify_lock()
    with pytest.raises(ValueError):
        previous_collection()
    with pytest.raises(ValueError):
        old()


@pytest.mark.parametrize("runtime", [RUNTIME, S1_RUNTIME])
def test_real_entry_wiring_with_synthetic_generator(
    worlds, tmp_path, monkeypatch, runtime
):
    from recoveragent import v2_episode as entry
    from freezegun import freeze_time
    import time

    task = plan()["feedback20"][0]
    model = {
        "model": "synthetic",
        "revision": "synthetic",
        "thinking": False,
        "chat_template_sha256": hashlib.sha256(
            Tokenizer.chat_template.encode()
        ).hexdigest(),
        "tool_format": "qwen_json",
    }
    contract = read_json("configs/v2_learning_64k_20261112_lock.json")[
        "session_contract"
    ]
    monkeypatch.setattr("recoveragent.v2_model.validate_identity", lambda x: x)
    monkeypatch.setattr(
        student, "verify_student_runtime", lambda _: {"session_contract": contract}
    )
    monkeypatch.setattr(entry, "environment_check", lambda: None)
    monkeypatch.setattr(
        entry, "AppWorldAdapter", lambda root, t, *a, **k: worlds(t, k["research"])
    )
    monkeypatch.setattr(entry, "load_tokenizer", lambda _: Tokenizer())
    monkeypatch.setattr(
        entry,
        "evaluate_isolated",
        lambda *a, **k: {"status": "valid", "success": False},
    )
    responses = iter(
        [
            block(
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            ),
            block("supervisor__complete_task", {}),
        ]
    )
    clock = {"now": 100.0}
    monkeypatch.setattr(time, "clock_gettime", lambda _: clock["now"])
    remaining = []

    def generate(request):
        remaining.append(request["remaining_wall_seconds"])
        clock["now"] += 17
        return receipt(next(responses))(request)

    with freeze_time("2024-01-01"):
        frozen = time.monotonic()
        result = entry.run(
            {
                "task_id": task,
                "output": str(tmp_path / "episode"),
                "appworld_root": "synthetic",
                "purpose": "feedback",
                "execution_protocol": runtime,
                "model": model,
                "checkpoint_id": "S0_synthetic",
                "student_limits": {
                    "max_requests": 80,
                    "wall_seconds": 1200,
                    "max_input_tokens": 65536,
                },
            },
            generate,
        )
        assert time.monotonic() == frozen
    assert remaining == [1200, 1183]
    assert result["wall_seconds"] == 34
    assert result["fault"] is None
    assert result["classification"] == "natural_failure"
    assert result["selected_snapshot"]["kind"] == "late_failure"
    source = result["selected_snapshot"]["snapshot_ref"]
    manifest, context, _ = state.inspect_snapshot(source)
    assert manifest["format_version"] == 6
    assert context["messages"][-1]["role"] in {"tool", "user"}
    assert result["usage"]["model_requests"] == 2
    restored = worlds(task, context_for(task))
    messages, tools, _ = restore_teacher_start(
        restored, source, tmp_path / "takeover", {}
    )
    assert messages[:-1] == context["messages"] and tools == context["tools"]
