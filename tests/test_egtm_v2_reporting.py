"""Budget/export regression with synthetic receipts; no model or environment score."""

import pytest

from recoveragent import student
from recoveragent.io import write_json
from scripts.summarize_egtm import summarize
from test_egtm_v2 import CONFIG, make_session, memory_reply, prime


def test_cost_report_counts_internal_requests_and_net_overhead(monkeypatch, tmp_path):
    s = make_session(monkeypatch, tmp_path / "base_sft_fixture_task_1/session")
    prime(s)
    s.step(memory_reply())
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
                    "status": "unknown",
                    "official_success": None,
                    "usage": s.usage,
                }
            ]
        },
    )
    result = summarize(tmp_path / "config.json", tmp_path)["rows"][0]
    assert result["model_requests"] == 4
    assert result["memory_update_requests"] == 1
    assert result["input_tokens"] == s.usage["prompt_tokens"]
    assert result["generated_tokens"] == s.usage["generated_tokens"]
    assert (
        result["memory_update_output_tokens"] == s.usage["memory_update_output_tokens"]
    )
    assert (
        result["net_input_saving_same_actions"]
        == -s.usage["memory_update_input_tokens"]
    )


def test_memory_state_cannot_reset_prior_generation_budgets(monkeypatch):
    s = make_session(monkeypatch)
    prime(s)
    s.step(memory_reply())
    with pytest.raises(ValueError, match="reset budgets"):
        student.StudentSession(
            s.adapter,
            s.tokenizer,
            s.messages,
            s.tools,
            tool_mode=s.tool_mode,
            memory_config=CONFIG,
            memory_state=s.memory,
            protocol_harness=True,
        )


def test_disabled_memory_does_not_change_original_clock_checks(monkeypatch):
    ticks = iter([0.0, 0.0, 1000.0])
    s = make_session(
        monkeypatch, enabled=False, clock=lambda: next(ticks), wall_seconds=600
    )
    # Original disabled runner checks once before compiling, not again afterwards.
    assert s.build_request()["max_new_tokens"] == 4096
    with pytest.raises(ValueError, match="budget"):
        s.build_request()


@pytest.mark.parametrize(
    "content,allowed", [("not JSON", False), ("null", True), ('{"id":1,"id":2}', False)]
)
def test_null_and_ambiguous_tool_results_are_not_conflated(content, allowed):
    from recoveragent.io import canonical
    from test_egtm_v2 import memory, append, l1

    mem, history = memory()
    append(mem, history, "notes__show", {"note_id": 7}, content)
    value = l1()
    value["important_facts"] = [
        {"evidence_ids": ["c0"], "path": [], "value": None, "status": "observed"}
    ]
    if allowed:
        assert mem.parse_l1(canonical(value), len(history)) == value
    else:
        with pytest.raises(ValueError, match="unambiguous JSON"):
            mem.parse_l1(canonical(value), len(history))


def test_restore_without_l1_cannot_reset_prior_tool_budgets(monkeypatch):
    s = make_session(monkeypatch)
    prime(s)
    assert s.memory.l1 is None and not s.memory.updates
    with pytest.raises(ValueError, match="reset budgets"):
        student.StudentSession(
            s.adapter,
            s.tokenizer,
            s.messages,
            s.tools,
            tool_mode=s.tool_mode,
            memory_config=CONFIG,
            memory_state=s.memory,
            protocol_harness=True,
        )
