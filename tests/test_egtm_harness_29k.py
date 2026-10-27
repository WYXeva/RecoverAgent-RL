"""Deterministic protocol fixtures; no model or official task score."""

import copy
import hashlib

from recoveragent import student

import pytest

from recoveragent.context_compiler import ContextBudgetError, compile_context, encode
from recoveragent.io import canonical, read_json
from recoveragent.student import StudentSession
from recoveragent.task_memory import TaskMemory, HARNESS_VERSION, memory_config
from recoveragent.teacher_history import validate_history
from test_egtm import INITIAL, TOOLS, append
from test_student_batch import Adapter as BaseAdapter
from test_student_batch import Tokenizer, block, receipt


@pytest.fixture(autouse=True)
def synthetic_template(monkeypatch):
    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )


class Adapter(BaseAdapter):
    catalogue = TOOLS


CONFIG = memory_config({"enabled": True, "version": HARNESS_VERSION})


def build(length):
    def make(last):
        messages = copy.deepcopy(INITIAL)
        memory = TaskMemory("task_1", messages, CONFIG)
        for n in [10000, 10000, last]:
            append(
                memory,
                messages,
                "notes__show",
                {"note_id": 7},
                canonical({"text": "x" * n}),
            )
        return memory, messages

    m, h = make(0)
    extra = length - len(encode(Tokenizer(), h, TOOLS))
    assert extra >= 0
    return make(extra)


def compile_now(memory, messages):
    return compile_context(
        Tokenizer(), messages, TOOLS, memory, max_tokens=32768, reserve_tokens=4096
    )


@pytest.mark.parametrize("length", [28673, 28999, 29000])
def test_exact_input_trigger_and_pairs(length):
    memory, messages = build(length)
    original = copy.deepcopy(messages)
    kept, ids, rec = compile_now(memory, messages)
    assert rec["pre_compaction_tokens"] == length
    assert rec["compression_triggered"] == (length >= 29000)
    assert len(ids) + rec["reserve_tokens"] <= 32768
    assert messages == original
    assert all(m in kept for m in INITIAL)
    validate_history(kept)
    if length < 29000:
        assert kept == messages and len(ids) == length and memory.compaction is None
    else:
        assert (
            len(ids) < length
            and memory.compaction["through"] == memory.batches[0]["end"]
        )
        assert memory.model_driven is False


def test_no_recompaction_until_current_input_again_reaches_threshold(tmp_path):
    memory, messages = build(29000)
    compile_now(memory, messages)
    first = copy.deepcopy(memory.compaction)
    append(memory, messages, "notes__show", {"note_id": 8}, '{"id":8}')
    kept, _, rec = compile_now(memory, messages)
    assert rec["compressed"] and not rec["compression_triggered"]
    assert memory.compaction == first
    assert rec["pre_compaction_tokens"] < 29000
    # Complete history is already >29k; it must NOT cause another removal.
    assert rec["original_tokens"] > 29000
    assert messages[-2:] == kept[-2:]
    memory.save(tmp_path / "memory", messages, TOOLS)
    restored = TaskMemory.load(tmp_path / "memory", messages, TOOLS)
    assert restored.identity == memory.identity
    assert compile_now(restored, messages)[0:2] == compile_now(memory, messages)[0:2]
    append(
        memory,
        messages,
        "notes__show",
        {"note_id": 9},
        canonical({"text": "y" * 15000}),
    )
    _, _, rec = compile_now(memory, messages)
    assert rec["compression_triggered"] and rec["pre_compaction_tokens"] >= 29000
    assert memory.compaction["through"] > first["through"]


def test_failure_is_atomic_and_no_truncation():
    memory, messages = build(29000)
    # Non-removable original task constraints alone cannot fit.
    messages[0]["content"] += "z" * 40000
    memory.initial_messages[0]["content"] = messages[0]["content"]
    # This alters history provenance and is rejected before compilation.
    with pytest.raises(ValueError, match="source history"):
        compile_now(memory, messages)
    assert memory.compaction is None
    m = TaskMemory("task_1", [{"role": "user", "content": "x" * 30000}], CONFIG)
    with pytest.raises(ContextBudgetError, match="older complete batch"):
        compile_now(m, m.initial_messages)
    assert m.compaction is None


def test_compaction_future_or_partial_boundary_rejected(tmp_path):
    memory, messages = build(29000)
    compile_now(memory, messages)
    memory.save(tmp_path / "memory", messages, TOOLS)
    state = read_json(tmp_path / "memory/state.json")
    state["compaction"]["through"] -= 1
    (tmp_path / "memory/state.json").write_text(canonical(state))
    with pytest.raises(ValueError, match="compaction boundary"):
        TaskMemory.load(tmp_path / "memory", messages, TOOLS)


def test_session_never_requests_model_memory_and_off_matches_before_trigger():
    a = Adapter()
    a.task_id = "task_1"
    s = StudentSession(
        a,
        Tokenizer(),
        INITIAL,
        TOOLS,
        tool_mode="dynamic_discovery",
        memory_config=CONFIG,
    )
    off = StudentSession(
        Adapter(), Tokenizer(), INITIAL, TOOLS, tool_mode="dynamic_discovery"
    )
    request = s.build_request()
    plain = off.build_request()
    assert "purpose" not in request
    assert {k: v for k, v in request.items() if k != "memory_compilation"} == plain
    # Even a public write/error does not ask the model for L1.
    s.step(receipt(block("notes__show", {"note_id": 7})))
    assert "purpose" not in s.build_request()
    assert s.usage.get("memory_update_requests", 0) == 0
    assert s.usage["compression_events"] == 0


def test_student_reduces_generation_below_trigger_without_early_compression():
    memory, messages = build(28999)
    a = Adapter()
    a.task_id = "task_1"
    s = StudentSession(
        a,
        Tokenizer(),
        messages,
        TOOLS,
        tool_mode="dynamic_discovery",
        memory_state=memory,
        memory_config=CONFIG,
    )
    req = s.build_request()
    assert len(req["input_token_ids"]) == 28999
    assert req["max_new_tokens"] == 3769
    assert not req["memory_compilation"]["compression_triggered"]


def test_frozen_threshold_and_no_model_fields():
    with pytest.raises(ValueError, match="fixes its input threshold"):
        memory_config({"version": HARNESS_VERSION, "trigger_tokens": 28000})
    with pytest.raises(ValueError, match="Unsupported"):
        memory_config({"version": HARNESS_VERSION, "update_max_new_tokens": 2048})
