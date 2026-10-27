"""Synthetic model receipts only: protocol/budget/provenance, not Qwen ability."""

import copy
import hashlib

import pytest

from recoveragent import student
from recoveragent.context_compiler import (
    ContextBudgetError,
    compile_context,
    encode,
    relevant_evidence,
)
from recoveragent.io import canonical, digest, read_json, write_json
from recoveragent.p3c import agent_runtime, drive, save_boundary
from recoveragent.state import capture, inspect_snapshot
from recoveragent.task_memory import (
    MODEL_VERSION,
    MODEL_RUNTIME,
    TaskMemory,
    memory_config,
)
from recoveragent.teacher_history import validate_history
from test_egtm import INITIAL, TOOLS, append, doc
from test_student_batch import Adapter, Tokenizer, DISCOVER, LOGIN, block, receipt
from test_p3c import attach_state

CONFIG = memory_config({"version": MODEL_VERSION, "enabled": True})


def l1(source=None):
    claim = {
        "text": "Inspect then update note 7; completion is not verified.",
        "status": "unverified",
        "evidence_ids": [source] if source else [],
    }
    return {
        "goal": "Update note 7",
        "constraints": ["Keep ALL files, paths and dates exactly"],
        "progress": claim,
        "completed": [],
        "pending": [claim],
        "important_facts": [],
        "uncertainties": [],
        "errors": [],
        "need_evidence": [],
    }


def memory():
    history = copy.deepcopy(INITIAL)
    return TaskMemory("task_1", history, CONFIG), history


def commit(mem, history, value, number=1, finish="stop"):
    text = canonical(value) if not isinstance(value, str) else value
    messages = mem.update_messages(history)
    request = {
        "purpose": "memory_update",
        "tools": [],
        "messages": messages,
        "policy": {"checkpoint_id": "SYNTHETIC"},
        "eos_token_id": 0,
        "input_token_ids": encode(Tokenizer(), messages, []),
        "max_new_tokens": 2048,
    }
    response = receipt(text, finish)(request)
    response["text"] = Tokenizer().decode(response["token_ids"])
    try:
        if finish != "stop":
            raise ValueError("Incomplete Memory output")
        mem.parse_l1(response["text"], len(history))
    except ValueError as exc:
        status, error = "rejected", str(exc)
    else:
        status, error = "accepted", None
    update = {
        "request_id": f"{number:03d}",
        "history_end": len(history),
        "history_sha256": digest(history),
        "old_l1_sha256": digest(mem.l1),
        "tools": TOOLS,
        "request": request,
        "response": response,
        "status": status,
        "error": error,
    }
    mem.record_update(history, TOOLS, update)
    return update


def make_session(monkeypatch, output=None, enabled=True, **limits):
    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )
    adapter = Adapter()
    adapter.task_id = "task_1"
    return student.StudentSession(
        adapter,
        Tokenizer(),
        INITIAL,
        [DISCOVER, LOGIN],
        output=output,
        tool_mode="dynamic_discovery",
        protocol_harness=True,
        memory_config={**CONFIG, "enabled": enabled},
        **limits,
    )


def memory_reply(value=None, finish="stop"):
    def generate(request):
        assert request["purpose"] == "memory_update" and request["tools"] == []
        text = canonical(l1() if value is None else value)
        response = receipt(text, finish)(request)
        response["text"] = Tokenizer().decode(response["token_ids"])
        return response

    return generate


def prime(session):
    for _ in range(3):
        session.step(receipt(block("demo__login", {})))


def test_model_l1_exact_values_unverified_judgments_and_incremental_input():
    mem, history = memory()
    doc(mem, history, "show", "GET")
    append(mem, history, "notes__show", {"note_id": 7}, '{"id":7,"path":"/A/007.txt"}')
    value = l1("c1")
    value["important_facts"] = [
        {
            "evidence_ids": ["c1"],
            "path": ["path"],
            "value": "/A/007.txt",
            "status": "observed",
        }
    ]
    commit(mem, history, value)
    assert mem.l1 == value and mem.working_view()["l1"] == value
    previous_end = len(history)
    append(mem, history, "notes__update", {"note_id": 7}, "{}")
    payload = __import__("json").loads(mem.update_messages(history)[1]["content"])
    assert payload["new_history"] == history[previous_end:]
    assert payload["old_l1"] == value and payload["OriginalGoal"] == INITIAL
    assert mem.l1["important_facts"][0]["status"] == "observed"  # receipt immutable
    assert "c1" in mem.working_view()["invalidated_evidence_ids"]
    value["important_facts"][0]["status"] = "stale"
    commit(mem, history, value, 2)
    assert mem.l1["important_facts"][0]["status"] == "stale"


@pytest.mark.parametrize(
    "kind",
    [
        "json",
        "duplicate",
        "future",
        "invented",
        "typed",
        "verified",
        "unknown_key",
        "length",
    ],
)
def test_bad_updates_preserve_old_l1(kind):
    mem, history = memory()
    append(mem, history, "notes__show", {"note_id": 7}, '{"id":7}')
    commit(mem, history, l1("c0"))
    before = copy.deepcopy(mem.l1)
    bad, finish = l1("c0"), "stop"
    if kind == "json":
        bad = "{broken"
    elif kind == "duplicate":
        bad = '{"goal":"x",' + canonical(bad)[1:]
    elif kind == "future":
        bad["progress"]["evidence_ids"] = ["future_call"]
    elif kind in {"invented", "typed"}:
        bad["important_facts"] = [
            {
                "evidence_ids": ["c0"],
                "path": ["id"],
                "value": 8 if kind == "invented" else "7",
                "status": "observed",
            }
        ]
    elif kind == "verified":
        bad["progress"]["status"] = "verified"
    elif kind == "unknown_key":
        bad["official_success"] = True
    else:
        finish = "length"
    result = commit(mem, history, bad, 2, finish)
    assert result["status"] == "rejected" and mem.l1 == before
    assert mem.covered_end == len(history)


def test_need_evidence_prioritizes_exact_visible_ids_and_no_future():
    mem, history = memory()
    append(mem, history, "notes__show", {"note_id": 7}, '{"id":7,"date":"old"}')
    cutoff = len(history)
    value = l1("c0")
    value["need_evidence"] = [{"source_id": "c0", "field": "date", "entity_id": 7}]
    commit(mem, history, value)
    append(mem, history, "notes__show", {"note_id": 8}, '{"id":8,"date":"future"}')
    rows = relevant_evidence(mem, history[:cutoff], cutoff)
    assert rows[0]["source_id"] == "c0" and all(
        r["boundary_end"] <= cutoff for r in rows
    )
    with pytest.raises(ValueError, match="Future|boundaries"):
        mem.validate_boundary(history[:cutoff], TOOLS)


def test_compiler_only_drops_l1_covered_batches_and_keeps_constraints():
    mem, history = memory()
    # Four independent big results, each small enough for an incremental update.
    for i in range(4):
        append(
            mem,
            history,
            "notes__show",
            {"note_id": i},
            canonical({"body": str(i) * 6000}),
        )
        commit(mem, history, l1(f"c{i}"), i + 1)
    mem.config["trigger_tokens"] = 2000
    append(mem, history, "notes__show", {"note_id": 4}, '{"id":4}')
    before = digest(history)
    compiled, ids, report = compile_context(
        Tokenizer(), history, TOOLS, mem, max_tokens=32768, reserve_tokens=4096
    )
    assert report["compressed"] and len(ids) < report["original_tokens"]
    assert compiled[:2] == INITIAL and digest(history) == before
    assert all(b["end"] <= mem.covered_end for b in report["omitted_batches"])
    assert compiled[-2:] == history[-2:]
    validate_history(compiled)
    # Without accepted L1 none of this history can be removed by rule summaries.
    empty = TaskMemory("task_1", INITIAL, CONFIG)
    huge = copy.deepcopy(INITIAL)
    for i in range(4):
        append(
            empty, huge, "notes__show", {"note_id": i}, canonical({"body": "x" * 10000})
        )
    with pytest.raises(ContextBudgetError):
        compile_context(
            Tokenizer(), huge, TOOLS, empty, max_tokens=32768, reserve_tokens=4096
        )


def test_memory_requests_charge_shared_budgets_raw_receipts_and_time(
    monkeypatch, tmp_path
):
    now = [0.0]
    s = make_session(
        monkeypatch, tmp_path / "session", max_requests=4, clock=lambda: now[0]
    )
    prime(s)
    before_history, tools_before = copy.deepcopy(s.messages), s.usage["tool_calls"]
    before_tokens = s.usage["generated_tokens"]

    def generate(request):
        now[0] += 2.5
        return memory_reply()(request)

    event = s.step(generate)
    assert event["status"] == "memory_boundary" and event["memory_status"] == "accepted"
    assert s.messages == before_history and s.usage["tool_calls"] == tools_before == 3
    assert s.usage["model_requests"] == 4 and s.usage["memory_update_requests"] == 1
    assert (
        s.usage["memory_update_output_tokens"]
        == s.usage["generated_tokens"] - before_tokens
    )
    assert s.usage["memory_update_seconds"] == 2.5
    request = read_json(tmp_path / "session/requests/004.request.json")
    response = read_json(tmp_path / "session/requests/004.response.json")
    assert request["policy"] == response["policy"] == s.identity
    assert s.usage["memory_update_input_tokens"] == len(request["input_token_ids"])
    assert s.usage["prompt_tokens"] == sum(
        len(read_json(p)["input_token_ids"])
        for p in (tmp_path / "session/requests").glob("*.request.json")
    )
    with pytest.raises(ValueError, match="budget"):
        s.step(lambda _: pytest.fail("Must not exceed total request budget"))


def test_bad_json_no_immediate_retry_and_token_limit(monkeypatch):
    s = make_session(monkeypatch)
    prime(s)
    remaining = 100
    s.max_generated_tokens = s.usage["generated_tokens"] + remaining

    def truncated(request):
        assert request["max_new_tokens"] == remaining
        result = receipt('{"goal":', finish="length")(request)
        result["text"] = Tokenizer().decode(result["token_ids"])
        return result

    s.step(truncated)
    assert s.memory.l1 is None and s.usage["memory_validation_failures"] == 1
    assert s.build_request().get("purpose") is None  # no internal retry at same history
    assert len(s.memory.updates) == 1


def test_time_limit_and_transport_failure_never_retried(monkeypatch):
    now = [0.0]
    s = make_session(monkeypatch, clock=lambda: now[0], wall_seconds=3)
    prime(s)

    def slow(request):
        now[0] = 4
        return memory_reply()(request)

    s.step(slow)
    assert s.usage["memory_update_seconds"] == 4
    with pytest.raises(ValueError, match="budget"):
        s.build_request()
    s = make_session(monkeypatch)
    prime(s)
    with pytest.raises(ConnectionError):
        s.step(lambda _: (_ for _ in ()).throw(ConnectionError()))
    assert s.stopped and s.memory.l1 is None and not s.memory.updates
    assert s.usage["unknown_generation_reservation"] == CONFIG["update_max_new_tokens"]
    assert s.usage["memory_update_requests"] == 1


def test_disabled_memory_identical_to_original_student(monkeypatch):
    s = make_session(monkeypatch, enabled=False)
    baseline = student.StudentSession(
        s.adapter,
        Tokenizer(),
        INITIAL,
        [DISCOVER, LOGIN],
        tool_mode="dynamic_discovery",
        protocol_harness=True,
    )
    for _ in range(5):
        assert s.build_request() == baseline.build_request()
        s.step(receipt(block("demo__login", {})))
        baseline.step(receipt(block("demo__login", {})))
    assert s.messages == baseline.messages and s.usage == baseline.usage


def test_v4_checkpoint_receipts_replayed_not_regenerated_and_tamper_rejected(
    monkeypatch, tmp_path
):
    s = make_session(monkeypatch)
    attach_state(s.adapter)
    s.adapter.task_id = "task_1"
    prime(s)
    s.step(memory_reply())
    pre = save_boundary(s, tmp_path / "pre", "pre_action", {})
    manifest, context, _ = inspect_snapshot(pre["snapshot_ref"])
    assert manifest["format_version"] == 4
    restored = TaskMemory.load(
        tmp_path / "pre/memory", context["messages"], context["tools"]
    )
    assert restored.identity == s.memory.identity and restored.l1 == s.memory.l1
    s.step(receipt(block("demo__login", {})))
    assert (
        TaskMemory.load(
            tmp_path / "pre/memory", context["messages"], context["tools"]
        ).identity
        == restored.identity
    )
    with pytest.raises(ValueError, match="boundaries|history"):
        s.memory.validate_boundary(context["messages"], context["tools"])
    # Even rehashing the checkpoint cannot make a fabricated L1 equal its raw response.
    state = read_json(tmp_path / "pre/memory/state.json")
    state["l1"]["goal"] = "invented goal"
    write_json(tmp_path / "altered/memory/state.json", state)
    import shutil

    shutil.copytree(
        tmp_path / "pre/memory/updates", tmp_path / "altered/memory/updates"
    )
    shutil.copyfile(
        tmp_path / "pre/memory/index.jsonl", tmp_path / "altered/memory/index.jsonl"
    )
    with pytest.raises(ValueError, match="original evidence"):
        TaskMemory.load(
            tmp_path / "altered/memory", context["messages"], context["tools"]
        )
    runtime = agent_runtime(s)
    runtime["usage"]["unknown_generation_reservation"] = 100
    with pytest.raises(ValueError, match="Unresolved"):
        capture(
            s.adapter,
            tmp_path / "unknown",
            s.messages,
            s.tools,
            "pre_action",
            {},
            agent_runtime=runtime,
            task_memory=s.memory,
        )


def test_p3c_internal_update_is_legal_boundary_not_task_action(monkeypatch, tmp_path):
    s = make_session(monkeypatch, max_requests=5)
    attach_state(s.adapter)
    s.adapter.task_id = "task_1"

    def generate(request):
        return (
            memory_reply()(request)
            if request.get("purpose")
            else receipt(block("demo__login", {}))(request)
        )

    result = drive(s, generate, tmp_path, {})
    assert result["fault"] is None
    assert s.usage["model_requests"] == 5 and s.usage["tool_calls"] == 4
    assert s.memory.l1 and s.events[3]["purpose"] == "memory_update"
    assert inspect_snapshot(tmp_path / "checkpoints/004_post")[0]["format_version"] == 4


def test_v2_configs_pair_only_memory_switch_and_frozen_policy():
    a, b = (read_json(f"configs/egtm_v2_eval_{arm}.json") for arm in "ab")
    assert not a["memory"]["enabled"] and b["memory"]["enabled"]
    a["memory"]["enabled"] = True
    assert a == b and b["runtime_revision"] == MODEL_RUNTIME
    assert b["expected_policy"]["checkpoint_id"].startswith("adapter:")


def test_partial_write_never_updates_l1_or_index(monkeypatch, tmp_path):
    s = make_session(monkeypatch)
    attach_state(s.adapter)
    s.adapter.task_id = "task_1"
    prime(s)
    s.step(memory_reply())
    before = s.memory.identity
    original = s.adapter.execute

    def execute(name, arguments, tools):
        s.adapter.value += 1
        result = original(name, arguments, tools)
        if s.adapter.value == 2:
            raise OSError("lost return after real side effect (software fixture)")
        return result

    s.adapter.execute = execute
    with pytest.raises(OSError):
        s.step(receipt(block("demo__login", {}) + block("demo__login", {})))
    assert s.adapter.value == 2 and s.memory.identity == before
    assert s.events[-1]["dispatched_count"] == 1 and s.events[-1]["inflight_call_id"]
    with pytest.raises(ValueError, match="unfinished batch"):
        save_boundary(s, tmp_path / "illegal", "post_action", {})


def test_rejected_update_replay_and_pre_error_no_future_memory(tmp_path):
    mem, history = memory()
    append(mem, history, "notes__show", {"note_id": 7}, '{"id":7}')
    commit(mem, history, l1("c0"))
    mem.save(tmp_path / "pre", history, TOOLS)
    pre = copy.deepcopy(history)
    append(
        mem,
        history,
        "notes__show",
        {"note_id": 8},
        "Execution failed. Response status code is 404",
    )
    commit(mem, history, "broken", 2)
    mem.save(tmp_path / "post", history, TOOLS)
    loaded = TaskMemory.load(tmp_path / "post", history, TOOLS)
    assert loaded.identity == mem.identity and loaded.covered_end == len(pre)
    early = TaskMemory.load(tmp_path / "pre", pre, TOOLS)
    assert len(early.updates) == 1 and "c1" not in early.index.keys["source_id"]
    payload = __import__("json").loads(loaded.update_messages(history)[1]["content"])
    assert payload["new_history"] == history[len(pre) :]


def test_required_evidence_cannot_be_silently_dropped():
    mem, history = memory()
    for i in range(3):
        append(
            mem, history, "notes__show", {"note_id": i}, canonical({"body": "a" * 4000})
        )
    value = l1()
    value["need_evidence"] = [{"source_id": "c0"}]
    commit(mem, history, value)
    # Use a smaller window to show that required retrieval is a hard boundary.
    with pytest.raises(ContextBudgetError):
        compile_context(
            Tokenizer(), history, TOOLS, mem, max_tokens=12000, reserve_tokens=4096
        )


def test_threshold_trigger_throttles_and_failed_update_cannot_lose_uncovered_history():
    mem, history = memory()
    doc(mem, history, "show", "GET")
    append(mem, history, "notes__show", {"note_id": 7}, '{"id":7}')
    assert mem.update_reason(21000) is None
    append(mem, history, "notes__show", {"note_id": 8}, '{"id":8}')
    assert mem.update_reason(21000) == "context_threshold"
    commit(mem, history, "invalid")
    assert mem.update_reason(40000) is None
    assert mem.covered_end == len(INITIAL)
    with pytest.raises(ContextBudgetError):
        compile_context(
            Tokenizer(), history, TOOLS, mem, max_tokens=3000, reserve_tokens=2048
        )


def test_internal_request_too_large_is_explicit_failure_not_generation(monkeypatch):
    s = make_session(monkeypatch, max_input_tokens=9000)
    prime(s)
    # NewHistory is legal but too large even for a memory update.
    s.messages.append({"role": "user", "content": "constraint" * 2000})
    before = digest(s.messages)
    with pytest.raises(ContextBudgetError, match="Memory request cannot fit"):
        s.step(lambda _: pytest.fail("No unsafe input dispatch"))
    assert s.usage["model_requests"] == 3 and digest(s.messages) == before
    assert s.termination == "context_budget_exhausted"
