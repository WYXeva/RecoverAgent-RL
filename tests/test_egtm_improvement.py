"""Synthetic protocol tests, NOT evidence of actual Qwen semantic quality."""

import copy
import hashlib
import json

import pytest

from recoveragent import student
from recoveragent.context_compiler import ContextBudgetError, compile_context, encode
from recoveragent.io import canonical, digest, read_json, write_json
from recoveragent.task_memory import (
    TaskMemory,
    VISIBLE_VERSION,
    VISIBLE_RUNTIME,
    memory_config,
)
from recoveragent.teacher_history import validate_history
from scripts.summarize_egtm import paired_report
from test_egtm import INITIAL, TOOLS, append, doc
from test_egtm_v2 import l1, commit, memory_reply, prime
from test_student_batch import Adapter, Tokenizer, DISCOVER, LOGIN, block, receipt

CONFIG = memory_config({"version": VISIBLE_VERSION, "enabled": True})


def memory():
    history = copy.deepcopy(INITIAL)
    return TaskMemory("task_1", history, CONFIG), history


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


def payload(messages):
    return next(
        json.loads(m["content"])
        for m in messages
        if '"kind":"internal_model_memory_data"' in m.get("content", "")
    )


def compile(mem, history, **kwargs):
    return compile_context(
        Tokenizer(),
        history,
        TOOLS,
        mem,
        max_tokens=kwargs.get("max_tokens", 32768),
        reserve_tokens=4096,
    )


def test_accepted_l1_visible_below_threshold_without_compression_or_duplicate_evidence():
    mem, history = memory()
    append(
        mem,
        history,
        "notes__show",
        {"note_id": 7},
        '{"id":7,"release_date":"2020-01-01"}',
    )
    value = l1("c0")
    value["need_evidence"] = [{"source_id": "c0", "field": "release_date"}]
    commit(mem, history, value)
    original = copy.deepcopy(history)
    compiled, ids, audit = compile(mem, history)
    assert audit["l1_visible"] and not audit["compressed"]
    assert len(ids) > audit["original_tokens"] and len(ids) + 4096 <= 32768
    view = payload(compiled)
    assert view["working_memory"]["l1"] == value and not view["historical_evidence"]
    assert "NOT a new user request" in view["interpretation"]
    assert compiled[:2] == INITIAL and history == original
    assert [
        m
        for m in compiled
        if m
        not in [
            next(
                m
                for m in compiled
                if '"kind":"internal_model_memory_data"' in m.get("content", "")
            )
        ]
    ] == history
    validate_history(compiled)


def test_model_session_uses_l1_on_very_next_business_request(monkeypatch, tmp_path):
    s = make_session(monkeypatch, tmp_path / "session")
    prime(s)
    s.step(memory_reply())
    request = s.build_request()
    assert request.get("purpose") is None
    assert payload(request["messages"])["working_memory"]["l1"] == s.memory.l1
    assert request["tools"] == s.tools and request["input_token_ids"] == encode(
        Tokenizer(), request["messages"], s.tools
    )
    assert not request["memory_compilation"]["compressed"]
    s.step(receipt(block("demo__login", {})))
    assert s.usage["saved_input_tokens"] < 0  # visibility overhead must be counted too


def test_projection_exact_nested_path_hash_and_typed_value_with_no_future():
    mem, history = memory()
    append(
        mem,
        history,
        "notes__show",
        {"note_id": 7},
        canonical(
            {
                "items": [
                    {"id": 7, "release_date": "2020-01-01"},
                    {"id": 8, "release_date": "2021-02-03"},
                ],
                "large": "x" * 12000,
            }
        ),
    )
    cutoff = len(history)
    q = {"source_id": "c0", "path": ["items", 1, "release_date"]}
    packet = mem.index.retrieve(task_id="task_1", as_of=cutoff, queries=[q])[0]
    assert packet["representation"] == "projection" and "content" not in packet
    assert packet["fields"] == [
        {
            "path": ["items", 1, "release_date"],
            "value": "2021-02-03",
            "value_sha256": digest("2021-02-03"),
        }
    ]
    assert packet["reference"]["sha256"] == digest(history[-1])
    assert (
        packet["content_sha256"]
        == hashlib.sha256(history[-1]["content"].encode()).hexdigest()
    )
    named = mem.index.retrieve(
        task_id="task_1",
        as_of=cutoff,
        queries=[{"source_id": "c0", "field": "release_date"}],
    )[0]
    assert (
        len(named["fields"]) == 2
    )  # does not pretend row-level entity filter is an object join
    append(mem, history, "notes__show", {"note_id": 8}, '{"release_date":"future"}')
    assert not mem.index.retrieve(
        task_id="task_1", as_of=cutoff, queries=[{"source_id": "c1"}]
    )
    with pytest.raises(ValueError, match="task/time"):
        mem.index.retrieve(task_id="other", as_of=cutoff, queries=[q])


@pytest.mark.parametrize(
    "content", ["opaque result", '{"release_date":1,"release_date":2}']
)
def test_opaque_or_ambiguous_projection_keeps_raw_text_and_reference(content):
    mem, history = memory()
    append(mem, history, "notes__show", {"note_id": 7}, content)
    row = mem.index.retrieve(
        task_id="task_1",
        as_of=len(history),
        queries=[{"source_id": "c0", "field": "release_date"}],
    )
    if (
        not row
    ):  # opaque text has no indexed fields; exact source+path can still retrieve it
        row = mem.index.retrieve(
            task_id="task_1",
            as_of=len(history),
            queries=[{"source_id": "c0", "path": ["release_date"]}],
        )
    assert row[0]["representation"] == "raw" and row[0]["content"] == content
    assert row[0]["projection_unavailable"] == "opaque_or_ambiguous_json"


def test_current_result_and_equivalent_full_result_not_reinserted():
    mem, history = memory()
    for _ in range(2):
        append(mem, history, "notes__show", {"note_id": 7}, '{"id":7}')
    packets = mem.index.retrieve(
        task_id="task_1",
        as_of=len(history),
        queries=[{"source_id": "c1"}, {"source_id": "c0"}, {"source_id": "c0"}],
        present_ids=["c1"],
    )
    assert len(packets) == 1 and packets[0]["representation"] == "reference"
    assert packets[0]["content_in_history"] == "c1" and "content" not in packets[0]


def test_long_context_retrieves_small_exact_field_and_stale_overlay():
    mem, history = memory()
    doc(mem, history, "show", "GET")
    append(
        mem,
        history,
        "notes__show",
        {"note_id": 7},
        canonical({"release_date": "2020-01-01", "body": "x" * 15000}),
    )
    value = l1("c1")
    value["need_evidence"] = [{"source_id": "c1", "path": ["release_date"]}]
    commit(mem, history, value)
    append(mem, history, "notes__update", {"note_id": 7}, "{}")
    append(mem, history, "notes__show", {"note_id": 8}, '{"id":8}')
    mem.config["trigger_tokens"] = 10000
    compiled, ids, audit = compile(mem, history)
    packets = payload(compiled)["historical_evidence"]
    assert (
        audit["compressed"]
        and audit["l1_visible"]
        and len(ids) < audit["original_tokens"]
    )
    assert packets[0]["source_id"] == "c1" and packets[0]["validity"] == "stale"
    assert (
        packets[0]["representation"] == "projection"
        and packets[0]["fields"][0]["value"] == "2020-01-01"
    )
    assert all(p["source_id"] not in {"c2", "c3"} for p in packets)
    validate_history(compiled)
    assert all(b["end"] <= mem.covered_end for b in audit["omitted_batches"])


def test_no_l1_means_no_rule_summary_and_l1_never_hidden_for_budget():
    mem, history = memory()
    append(mem, history, "notes__show", {"note_id": 7}, '{"id":7}')
    assert compile(mem, history)[0] == history
    commit(mem, history, l1("c0"))
    with pytest.raises(ContextBudgetError, match="L1/required"):
        compile(
            mem, history, max_tokens=len(encode(Tokenizer(), history, TOOLS)) + 4096
        )


def test_update_input_deduplicates_delta_and_old_field_projection():
    mem, history = memory()
    append(mem, history, "notes__show", {"note_id": 7}, '{"release_date":"2020-01-01"}')
    value = l1("c0")
    value["need_evidence"] = [{"source_id": "c0", "path": ["release_date"]}]
    commit(mem, history, value)
    append(mem, history, "notes__show", {"note_id": 8}, '{"release_date":"new"}')
    data = json.loads(mem.update_messages(history)[1]["content"])
    assert data["new_history"] == history[mem.covered_end :]
    assert (
        len(data["relevant_evidence"]) == 1
        and data["relevant_evidence"][0]["representation"] == "projection"
    )
    assert data["relevant_evidence"][0]["source_id"] == "c0"


def test_context_pressure_can_trigger_before_three_batches_and_failure_not_retried(
    monkeypatch,
):
    s = make_session(monkeypatch)
    s.memory.config["trigger_tokens"] = 5000
    s.step(receipt(block("demo__login", {})))
    request = s.build_request()
    assert (
        request["purpose"] == "memory_update"
    )  # actual update template is larger than the actor input
    s.step(memory_reply(value={"bad": True}))
    assert s.memory.l1 is None and s.memory.covered_end == len(INITIAL)
    assert s.build_request().get("purpose") is None


def test_new_checkpoint_replay_preserves_original_v2(monkeypatch, tmp_path):
    s = make_session(monkeypatch)
    prime(s)
    s.step(memory_reply())
    s.memory.save(tmp_path / "new", s.messages, s.tools)
    restored = TaskMemory.load(tmp_path / "new", s.messages, s.tools)
    assert (
        restored.runtime == VISIBLE_RUNTIME and restored.identity == s.memory.identity
    )
    assert payload(s.build_request()["messages"])["working_memory"]["l1"] == restored.l1


def paired_fixture(tmp_path, rows=2, error=None, mismatch=None):
    base = read_json("configs/egtm_v2_improvement_pilot_a.json")
    base["cases"] = base["cases"][:2]
    configs = []
    for arm in "ab":
        config = copy.deepcopy(base)
        config["memory"]["enabled"] = arm == "b"
        path = tmp_path / f"{arm}_config.json"
        write_json(path, config)
        configs.append(path)
        directory = tmp_path / arm
        write_json(directory / "config.json", config)
        results = []
        for i, case in enumerate(config["cases"][:rows]):
            status = error if arm == "b" and i == 1 and error else "valid"
            row = {
                **case,
                "status": status,
                "official_success": False if status == "valid" else None,
                "initial_state_hash": f"db_{i}",
                "usage": {"prompt_tokens": 2, "generated_tokens": 1, "tool_calls": 0},
                "wall_seconds": 1,
            }
            if mismatch == "db" and arm == "b":
                row["initial_state_hash"] = "different"
            results.append(row)
            folder = (
                directory
                / f"base_sft_{case['partition']}_{case['task_id']}/session/requests"
            )
            req = {
                "policy": config["expected_policy"],
                "input_token_ids": [1, 2],
                "max_new_tokens": 1,
                "messages": INITIAL,
                "tools": TOOLS,
            }
            if mismatch == "input" and arm == "b":
                req["input_token_ids"] = [2, 3]
            if arm == "b":
                req["memory_compilation"] = {
                    "original_tokens": 2,
                    "compressed": False,
                    "l1_visible": False,
                }
            write_json(folder / "001.request.json", req)
            write_json(
                folder / "001.response.json",
                {
                    "policy": req["policy"],
                    "input_token_ids": req["input_token_ids"],
                    "token_ids": [3],
                },
            )
        write_json(directory / "summary.json", {"rows": results})
    return paired_report(configs[0], tmp_path / "a", configs[1], tmp_path / "b")


@pytest.mark.parametrize(
    "rows,error,status",
    [
        (0, None, "INCOMPLETE"),
        (1, None, "INCOMPLETE"),
        (2, None, "PASS"),
        (2, "evaluation_error", "INCOMPLETE"),
        (2, "infrastructure_error", "INCOMPLETE"),
    ],
)
def test_paired_full_denominator_and_unknowns_not_task_failures(
    tmp_path, rows, error, status
):
    report = paired_fixture(tmp_path, rows, error)
    assert report["status"] == status and len(report["paired_tasks"]) == 2
    assert report["B"]["task_failures"] == rows - int(bool(error))
    assert report["B"]["successes"] == 0
    assert not report["pilot_protocol_ready"]  # no real L1 was ever exercised


@pytest.mark.parametrize("mismatch", ["db", "input"])
def test_paired_initial_state_or_token_mismatch_cannot_pass(tmp_path, mismatch):
    report = paired_fixture(tmp_path, mismatch=mismatch)
    assert report["status"] == "FAIL" and len(report["mismatched_tasks"]) == 2


def test_paired_audit_checks_actual_l1_payload_not_just_visibility_flag(
    monkeypatch, tmp_path
):
    from scripts.summarize_egtm import summarize

    s = make_session(monkeypatch, tmp_path / "base_sft_fixture_task_1/session")
    prime(s)
    s.step(memory_reply())
    s.step(receipt(block("demo__login", {})))
    config = {
        "expected_policy": s.identity,
        "memory": CONFIG,
        "cases": [{"task_id": "task_1"}],
    }
    write_json(tmp_path / "config.json", config)
    write_json(
        tmp_path / "summary.json",
        {
            "rows": [
                {
                    "task_id": "task_1",
                    "partition": "fixture",
                    "status": "valid",
                    "official_success": False,
                    "usage": s.usage,
                }
            ]
        },
    )
    result = summarize(tmp_path / "config.json", tmp_path)["rows"][0]
    assert (
        result["l1_visible_requests"] == 1
        and result["accepted_l1_missing_requests"] == 0
    )
    assert result["memory_acceptance_rate"] == 1 and result["compressed_requests"] == 0
    path = tmp_path / "base_sft_fixture_task_1/session/requests/005.request.json"
    request = read_json(path)
    request["messages"] = [
        m
        for m in request["messages"]
        if '"kind":"internal_model_memory_data"' not in m.get("content", "")
    ]
    path.write_text(
        canonical(request)
    )  # deliberately corrupt fixture, keep the misleading flag
    corrupted = summarize(tmp_path / "config.json", tmp_path)["rows"][0]
    assert corrupted["accepted_l1_missing_requests"] == 1


def test_disabled_improved_memory_first_input_and_generation_identical(monkeypatch):
    a, b = (
        make_session(monkeypatch, enabled=False),
        make_session(monkeypatch, enabled=True),
    )
    ra, rb = a.build_request(), b.build_request()
    assert ra["input_token_ids"] == rb["input_token_ids"]
    assert all(
        ra[k] == rb[k]
        for k in (
            "policy",
            "messages",
            "tools",
            "seed",
            "temperature",
            "top_p",
            "max_new_tokens",
        )
    )
    plain = student.StudentSession(
        a.adapter,
        a.tokenizer,
        INITIAL,
        [DISCOVER, LOGIN],
        tool_mode="dynamic_discovery",
        protocol_harness=True,
    )
    assert a.build_request() == plain.build_request()


def test_failed_update_preserves_old_visible_l1_and_uncovered_interactions():
    mem, history = memory()
    append(mem, history, "notes__show", {"note_id": 7}, '{"id":7}')
    commit(mem, history, l1("c0"))
    old = copy.deepcopy(mem.l1)
    boundary = mem.covered_end
    append(mem, history, "notes__show", {"note_id": 8}, '{"id":8}')
    commit(mem, history, '{"bad":true}', 2)
    compiled, _, audit = compile(mem, history)
    assert payload(compiled)["working_memory"]["l1"] == old and audit["l1_visible"]
    assert compiled[-2:] == history[-2:] and mem.covered_end == boundary


def test_tokenizer_pipe_preserves_schema_key_order_for_qwen_template():
    import io
    from types import SimpleNamespace
    from scripts.probe_egtm import TokenizerPipe

    pipe = object.__new__(TokenizerPipe)
    pipe.process = SimpleNamespace(stdin=io.StringIO())
    pipe.read = lambda: "fixture"
    tools = [
        {"function": {"name": "demo", "parameters": {"properties": {"z": {}, "a": {}}}}}
    ]
    pipe.rpc("apply_chat_template", [], tools=tools, enable_thinking=False)
    sent = json.loads(pipe.process.stdin.getvalue())
    assert list(sent["kwargs"]["tools"][0]["function"]["parameters"]["properties"]) == [
        "z",
        "a",
    ]
