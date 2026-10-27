"""Software fixtures, not model/task scores. Real-world probes are separate."""

import copy
import hashlib

import pytest

from recoveragent import student
from recoveragent.context_compiler import ContextBudgetError, compile_context, encode
from recoveragent.harness import RULES
from recoveragent.io import canonical, digest, read_json
from recoveragent.p3c import agent_runtime, save_boundary
from recoveragent.state import inspect_snapshot, restore
from recoveragent.task_memory import TaskMemory, memory_config
from recoveragent.teacher_history import validate_history
from test_contracts import FakeAdapter
from test_student_batch import Adapter, Tokenizer, DISCOVER, LOGIN, block, receipt, tool

INITIAL = [
    {"role": "system", "content": RULES},
    {
        "role": "user",
        "content": "Keep ALL files, paths and dates exactly; update note 7.",
    },
]
CONFIG = memory_config({"enabled": True, "trigger_tokens": 2000})
DOC = tool(
    "api_docs__show_api_doc",
    {"app_name": {"type": "string"}, "api_name": {"type": "string"}},
)
SHOW = tool("notes__show", {"note_id": {"type": "integer"}})
WRITE = tool("notes__update", {"note_id": {"type": "integer"}})
TOOLS = [DOC, SHOW, WRITE]


def fixture_memory():
    messages = copy.deepcopy(INITIAL)
    return TaskMemory("task_1", messages, CONFIG), messages


def append(memory, messages, name, args, result, *, prose="", executed=True):
    number, start = len(memory.batches), len(messages)
    call = {
        "id": f"c{number}",
        "type": "function",
        "function": {"name": name, "arguments": canonical(args)},
    }
    result = {
        "role": "tool",
        "name": name,
        "tool_call_id": call["id"],
        "content": result,
    }
    event = {
        "status": "complete_boundary",
        "assistant_index": start,
        "request_id": str(number),
        "history_hash": digest(messages),
        "tools": TOOLS,
        "tool_results": [result],
        "dispatched_count": 1,
        "harness_verdicts": [None if executed else {"guard": True}],
    }
    messages.extend(
        [{"role": "assistant", "content": prose, "tool_calls": [call]}, result]
    )
    memory.observe(messages, event)
    return event


def doc(memory, messages, api, method):
    append(
        memory,
        messages,
        "api_docs__show_api_doc",
        {"app_name": "notes", "api_name": api},
        canonical({"app_name": "notes", "api_name": api, "method": method}),
    )


def test_exact_facts_sources_inference_and_unknown_write_invalidates():
    memory, messages = fixture_memory()
    doc(memory, messages, "show", "GET")
    append(
        memory,
        messages,
        "notes__show",
        {"note_id": 7},
        '{"id":7,"date":"2026-01-02","path":"/A/007.txt","balance":"$4.00"}',
        prose="I think everything is done",
    )
    facts = [f for f in memory.facts if f["kind"] == "result"]
    assert {f["status"] for f in facts} == {"observed"}
    assert [f["value"] for f in facts] == [7, "2026-01-02", "/A/007.txt", "$4.00"]
    assert memory.plans[-1]["status"] == "inferred"
    assert memory.operations[-1]["outcome"] == "returned"
    assert memory.working_view()["completed_task_requirements"].startswith("unknown")
    for fact in facts:
        assert (
            digest(messages[fact["evidence_ref"]["message_index"]])
            == fact["evidence_ref"]["sha256"]
        )
    append(
        memory,
        messages,
        "notes__update",
        {"note_id": 7},
        "Execution failed. Response status code is 500",
    )
    assert all(f["status"] == "stale" and f["invalidated_by"] == "c2" for f in facts)
    assert memory.operations[-1]["outcome"] == "error_observed"
    assert not any(f["tool_call_id"] == "c2" for f in memory.facts)


def test_temporal_typed_search_and_query_scope():
    memory, messages = fixture_memory()
    doc(memory, messages, "show", "GET")
    append(memory, messages, "notes__show", {"note_id": 7}, '{"id":7,"value":"old"}')
    cutoff = len(messages)
    append(memory, messages, "notes__show", {"note_id": 8}, '{"id":8,"value":"future"}')

    def search(**kw):
        return memory.index.search(task_id="task_1", as_of=cutoff, **kw)

    assert search(entity_id=7, field="value")[0]["source_id"] == "c1"
    assert (
        not search(entity_id="7")
        and not search(entity_id=8)
        and not search(source_id="c2")
    )
    assert len(search(tool_name="notes__show")) == 1
    with pytest.raises(ValueError, match="task/time"):
        memory.index.search(task_id="another_task", as_of=999)
    assert memory.operations[-1]["repeat_of"] is None
    append(memory, messages, "notes__show", {"note_id": 8}, "[]")
    assert memory.operations[-1]["repeat_of"] == "c2"
    assert all(
        f["status"] == "stale" for f in memory.facts if f["tool_call_id"] == "c2"
    )
    assert all(
        f["status"] == "observed" for f in memory.facts if f["tool_call_id"] == "c1"
    )
    assert (
        memory.operations[-1]["outcome"] == "returned"
    )  # NOT a global empty-page conclusion.


def test_guard_feedback_and_opaque_results_never_become_facts():
    memory, messages = fixture_memory()
    append(
        memory,
        messages,
        "notes__show",
        {"note_id": 7},
        '{"status":"success"}',
        executed=False,
    )
    assert not memory.facts and not memory.operations
    assert not memory.index.search(task_id="task_1", as_of=len(messages))
    append(memory, messages, "notes__show", {"note_id": 7}, "unparseable original text")
    assert (
        not memory.facts
        and memory.index.rows[-1]["content"] == "unparseable original text"
    )


def test_reject_partial_or_altered_batch_without_memory_update():
    memory, messages = fixture_memory()
    event = append(memory, messages, "notes__show", {"note_id": 7}, '{"id":7}')
    before = memory.identity
    with pytest.raises(ValueError):
        memory.observe(messages[:-1], event)
    with pytest.raises(ValueError):
        memory.observe(messages, {**event, "status": "interrupted_batch"})
    with pytest.raises(ValueError):
        memory.observe(messages, {**event, "history_hash": "altered"})
    assert memory.identity == before


def long_history():
    memory, messages = fixture_memory()
    doc(memory, messages, "show", "GET")
    append(
        memory,
        messages,
        "notes__show",
        {"note_id": 7},
        canonical({"id": 7, "body": "0123456789" * 2300}),
    )
    append(memory, messages, "notes__show", {"note_id": 8}, '{"id":8}')
    append(memory, messages, "notes__show", {"note_id": 9}, '{"id":9}')
    messages.append(
        {"role": "user", "content": "Harness feedback: preserve the original files."}
    )
    return memory, messages


def test_compiler_actually_reduces_tokens_preserves_constraints_pairs_and_history():
    memory, messages = long_history()
    before = copy.deepcopy(messages)
    compiled, tokens, audit = compile_context(
        Tokenizer(), messages, TOOLS, memory, max_tokens=32768, reserve_tokens=4096
    )
    assert audit["compressed"] and len(tokens) < audit["original_tokens"]
    assert len(tokens) + 4096 <= 32768 and messages == before
    assert compiled[:2] == INITIAL and compiled[-1] == messages[-1]
    assert [
        m
        for m in compiled
        if m["role"] in {"system", "user"} and "recoveragent_egtm" not in m["content"]
    ] == [m for m in messages if m["role"] in {"system", "user"}]
    validate_history(compiled)
    assert tokens == encode(Tokenizer(), compiled, TOOLS)
    assert audit["tools_sha256"] == digest(TOOLS)
    with pytest.raises(ValueError, match="boundaries|history"):
        compile_context(
            Tokenizer(),
            messages[:-3],
            TOOLS,
            memory,
            max_tokens=32768,
            reserve_tokens=4096,
        )


def test_cannot_silently_shorten_required_recent_batch_or_task_constraints():
    memory, messages = fixture_memory()
    append(
        memory,
        messages,
        "notes__show",
        {"note_id": 7},
        canonical({"body": "x" * 40000}),
    )
    before = digest(messages)
    with pytest.raises(ContextBudgetError, match="no truncation"):
        compile_context(
            Tokenizer(), messages, TOOLS, memory, max_tokens=32768, reserve_tokens=4096
        )
    assert digest(messages) == before


def test_memory_snapshot_recomputes_facts_not_just_self_reported_hashes(tmp_path):
    memory, messages = fixture_memory()
    append(memory, messages, "notes__show", {"note_id": 7}, '{"id":7}')
    memory.save(tmp_path / "memory", messages, TOOLS)
    loaded = TaskMemory.load(tmp_path / "memory", messages, TOOLS)
    assert loaded.identity == memory.identity
    with pytest.raises(ValueError, match="Future"):
        TaskMemory.load(tmp_path / "memory", INITIAL, TOOLS)
    state = read_json(tmp_path / "memory/state.json")
    state["facts"][0]["value"] = "invented answer"
    (tmp_path / "memory/state.json").write_text(canonical(state))
    with pytest.raises(ValueError, match="original evidence"):
        TaskMemory.load(tmp_path / "memory", messages, TOOLS)


def test_retrieval_recovers_exact_old_evidence_without_duplicate_payloads():
    from recoveragent.context_compiler import relevant_evidence

    memory, messages = fixture_memory()
    doc(memory, messages, "show", "GET")
    for _ in range(4):
        append(
            memory,
            messages,
            "notes__show",
            {"note_id": 7},
            '{"id":7,"path":"/keep/me"}',
        )
    rows = relevant_evidence(memory, messages, len(messages))
    matches = [r for r in rows if r["tool_name"] == "notes__show"]
    assert len(matches) == 1 and matches[0]["source_id"] == "c4"
    assert matches[0]["content"] == '{"id":7,"path":"/keep/me"}'


def test_paired_configs_only_change_memory_and_bind_the_same_model_and_plan():
    a, b = (read_json(f"configs/egtm_v1_eval_{arm}.json") for arm in ("a", "b"))
    assert a["memory"]["enabled"] is False and b["memory"]["enabled"] is True
    a["memory"]["enabled"] = True
    assert a == b and len(a["cases"]) == 12
    plan = read_json(a["task_manifest"])
    assert digest(plan) == a["task_manifest_hash"]
    assert [c["task_id"] for c in a["cases"]] == plan["tasks"]
    release = read_json(a["checkpoint_release"])
    assert (
        a["expected_policy"]["checkpoint_id"]
        == "adapter:" + release["adapter_tensor_sha256"]
    )


def session(monkeypatch, tmp_path=None, enabled=False, **kwargs):
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
        tool_mode="dynamic_discovery",
        protocol_harness=True,
        output=tmp_path,
        memory_config={**CONFIG, "enabled": enabled},
        **kwargs,
    )


def test_disabled_memory_preserves_exact_requests_actions_and_usage(monkeypatch):
    disabled = session(monkeypatch)
    original = student.StudentSession(
        disabled.adapter,
        Tokenizer(),
        INITIAL,
        [DISCOVER, LOGIN],
        tool_mode="dynamic_discovery",
        protocol_harness=True,
    )
    assert original.build_request() == disabled.build_request()
    for s in (original, disabled):
        s.step(receipt(block("demo__login", {})))
    assert original.build_request() == disabled.build_request()
    assert original.messages == disabled.messages and original.usage == disabled.usage


def test_session_protocol_rejections_partial_writes_and_audit(monkeypatch, tmp_path):
    s = session(monkeypatch, tmp_path / "session", enabled=True)
    s.step(receipt(block("demo__read", {"access_token": "guessed"})))
    assert not s.memory.batches and not s.adapter.calls
    s.step(receipt(block("demo__login", {})))
    assert len(s.memory.batches) == 1
    before = s.memory.identity
    count = 0

    def broken(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("side effect applied, response lost")
        return '{"access_token":"observed_token"}', s.tools

    s.adapter.execute = broken
    with pytest.raises(OSError):
        s.step(receipt(block("demo__login", {}) + block("demo__login", {})))
    assert s.memory.identity == before and s.termination == "interrupted_batch"
    assert s.events[-1]["dispatched_count"] == 1 and s.events[-1]["inflight_call_id"]
    assert (
        read_json(tmp_path / "session/requests/002.history.json")["messages"][0:2]
        == INITIAL
    )
    assert (tmp_path / "session/requests/003.response.json").exists()
    with pytest.raises(ValueError, match="unfinished"):
        s.memory.validate_boundary(s.messages, s.tools)


def test_v3_state_roundtrip_and_runtime_binding_old_v2_unmodified(
    tmp_path, monkeypatch
):
    s = session(monkeypatch, enabled=True)
    s.step(receipt(block("demo__login", {})))
    a = FakeAdapter()
    a.task_id = s.adapter.task_id
    a.world = s.adapter.world
    s.adapter = a
    source = tmp_path / "v3"
    save_boundary(s, source, "post_action", {"probe": True})
    manifest, context, _ = inspect_snapshot(source)
    assert manifest["format_version"] == 3
    a.value = 42
    restore(a, source)
    assert a.value == 1
    loaded = TaskMemory.load(source / "memory", context["messages"], context["tools"])
    assert loaded.identity == agent_runtime(s)["memory"]
    assert read_json(source / "agent_runtime.json")["memory"] == loaded.identity
    assert (
        read_json(source / "agent_runtime.json")["format"]
        == "egtm_task_memory_20261027"
    )
    (source / "memory/index.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="modified"):
        inspect_snapshot(source)


def test_pagination_filter_changes_do_not_reuse_empty_query_conclusions(monkeypatch):
    properties = SHOW["function"]["parameters"]["properties"]
    for name, kind in (
        ("page_index", "integer"),
        ("query", "string"),
        ("sort_by", "string"),
    ):
        monkeypatch.setitem(properties, name, {"type": kind})
    memory, messages = fixture_memory()
    doc(memory, messages, "show", "GET")
    query = {"note_id": 7, "page_index": 0, "query": "alpha", "sort_by": "+id"}
    append(memory, messages, "notes__show", query, '{"id":7}')
    append(memory, messages, "notes__show", {**query, "page_index": 1}, "[]")
    append(memory, messages, "notes__show", {**query, "query": "beta"}, "[]")
    append(memory, messages, "notes__show", {**query, "sort_by": "-id"}, "[]")
    assert all(o["repeat_of"] is None for o in memory.operations)
    assert all(
        f["status"] == "observed" for f in memory.facts if f["tool_call_id"] == "c1"
    )


def test_memory_active_mixed_illegal_batch_executes_nothing(monkeypatch):
    s = session(monkeypatch, enabled=True)
    before = s.memory.identity
    for _ in range(3):
        s.step(receipt(block("demo__login", {}) + block("demo__login", {"extra": 1})))
    assert not s.adapter.calls and s.memory.identity == before
    assert s.stopped and s.harness.rejections == 3  # Original + two correction chances.
    assert s.usage["model_requests"] == 3


def test_v3_cannot_drop_memory_or_claim_a_legacy_runtime(tmp_path, monkeypatch):
    from recoveragent.state import capture

    s = session(monkeypatch, enabled=True)
    runtime = agent_runtime(s)
    with pytest.raises(ValueError, match="omit Memory"):
        capture(
            s.adapter,
            tmp_path / "omit",
            s.messages,
            s.tools,
            "pre_action",
            {},
            agent_runtime=runtime,
        )
    with pytest.raises(ValueError, match="EGTM runtime"):
        capture(
            s.adapter,
            tmp_path / "wrong_version",
            s.messages,
            s.tools,
            "pre_action",
            {},
            agent_runtime={**runtime, "format": "legacy"},
            task_memory=s.memory,
        )
    assert (
        not (tmp_path / "omit").exists() and not (tmp_path / "wrong_version").exists()
    )


def test_array_entity_ids_and_opaque_evidence_validity(monkeypatch):
    from recoveragent.context_compiler import relevant_evidence

    monkeypatch.setitem(
        SHOW["function"]["parameters"]["properties"],
        "note_ids",
        {"type": "array", "items": {"type": "integer"}},
    )
    memory, messages = fixture_memory()
    doc(memory, messages, "show", "GET")
    append(
        memory,
        messages,
        "notes__show",
        {"note_id": 7, "note_ids": [8, 9]},
        '{"note_ids":[8,9],"body":"original"}',
    )
    found = memory.index.search(
        task_id="task_1", as_of=len(messages), entity_id=9, field="note_ids"
    )
    assert len(found) == 1 and found[0]["source_id"] == "c1"
    assert "c1" in [
        r["source_id"] for r in relevant_evidence(memory, messages, len(messages))
    ]
    append(memory, messages, "notes__show", {"note_id": 7}, "Opaque original output")
    assert not any(f["tool_call_id"] == "c2" for f in memory.facts)
    append(
        memory,
        messages,
        "notes__update",
        {"note_id": 7},
        "Execution failed. Response status code is 500",
    )
    assert memory.stale_sources["c2"] == "c3"
    assert "c2" in memory.working_view()["needs_revalidation"]


def test_evaluation_metrics_reconcile_receipts_and_keep_unknown_outcomes(tmp_path):
    from recoveragent.io import write_json
    from scripts.summarize_egtm import summarize

    config_path = "configs/egtm_v1_eval_a.json"
    config = read_json(config_path)
    case = config["cases"][0]
    folder = tmp_path / f"base_sft_{case['partition']}_{case['task_id']}" / "session"
    write_json(tmp_path / "config.json", config)
    write_json(
        folder / "requests/001.request.json",
        {
            "policy": config["expected_policy"],
            "input_token_ids": [1, 2],
            "max_new_tokens": 1,
        },
    )
    write_json(
        folder / "requests/001.response.json",
        {
            "policy": config["expected_policy"],
            "input_token_ids": [1, 2],
            "token_ids": [3],
        },
    )
    row = {
        **case,
        "status": "evaluation_error",
        "official_success": None,
        "usage": {"prompt_tokens": 2, "generated_tokens": 1, "tool_calls": 0},
    }
    write_json(tmp_path / "summary.json", {"rows": [row]})
    result = summarize(config_path, tmp_path)
    assert result["unknown_evaluations"] == 1 and result["success_rate_valid"] is None
    assert result["rows"][0]["input_tokens"] == 2
    response = read_json(folder / "requests/001.response.json")
    response["input_token_ids"] = [9]
    (folder / "requests/001.response.json").write_text(canonical(response))
    with pytest.raises(ValueError, match="receipt"):
        summarize(config_path, tmp_path)
