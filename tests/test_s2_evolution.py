"""Group evidence, overflow fallback and online admission; synthetic Teacher only."""

from pathlib import Path

import pytest

from recoveragent.coevolve_generation import (
    CONTEXT_FIELDS,
    CONTEXT_PROMPT,
    ContextTooLong,
)
from recoveragent.s2_evolution import GroupEvolution
from recoveragent.s2_schedule import GroupFeedback
from recoveragent.io import read_json, write_json
from test_s2_experiment import FakeRuntime, cfg, generated, make_group


def engine(tmp_path):
    config = cfg()
    return GroupEvolution(
        config,
        {},
        {"teacher_budget_amount": 100, "appworld_root": "synthetic"},
        tmp_path,
        {"teacher_usage_priced_usd": 0, "feedback": []},
        1e20,
        None,
        budget_root=tmp_path,
    )


def event(tmp_path):
    ledger = GroupFeedback(cfg()["task_evolution"]["signals"])
    ledger.observe(0, make_group(tmp_path, "before", [True] * 4))
    return ledger.observe(
        1, make_group(tmp_path, "current", [False, True, False, True])
    )


def test_context_has_all_four_histories_and_only_pre_group_success(
    tmp_path, monkeypatch
):
    e = engine(tmp_path)
    calls = []

    def text(source, directory, prompt, content):
        calls.append((prompt, content))
        return dict.fromkeys(CONTEXT_FIELDS, "synthetic observation")

    monkeypatch.setattr(e, "text", text)
    signal = event(tmp_path)
    result = e.context(signal, tmp_path / "context")
    assert set(result) == CONTEXT_FIELDS
    prompt, content = calls[0]
    assert prompt.startswith(CONTEXT_PROMPT)
    assert len(content["trajectories"]) == 4
    assert content["group"]["outcomes"] == [False, True, False, True]
    assert content["group"]["prior_success_groups"][0]["group_id"] == 0
    assert all("evaluation" not in t["trajectory"] for t in content["trajectories"])


def test_context_summarizes_only_measured_overflow_then_retains_four_refs(
    tmp_path, monkeypatch
):
    e = engine(tmp_path)
    calls = []

    def text(source, directory, prompt, content):
        calls.append((str(directory), content))
        if Path(directory).name == "context":
            raise ContextTooLong("measured")
        if "message_start" in content:
            return {
                "summary": "Observed actions",
                "evidence_refs": [{"message_index": content["message_start"]}],
            }
        return dict.fromkeys(CONTEXT_FIELDS, "summarized")

    monkeypatch.setattr(e, "text", text)
    e.context(event(tmp_path), tmp_path / "context")
    assert len(calls) == 6
    summaries = calls[-1][1]["traceable_summaries"]
    assert (
        len(summaries) == 4
        and len({r["trajectory"]["directory"] for r in summaries}) == 4
    )
    assert all(r["messages_sha256"] and r["message_count"] == 3 for r in summaries)


def test_two_explorations_one_abstraction_keeps_combined_cap_and_existing_prompt(
    tmp_path, monkeypatch
):
    from test_coevolve import history, reference

    e = engine(tmp_path)
    calls, executed = [], []

    def execute(source, directory, payload):
        executed.append(payload)
        if payload["mode"] == "validation":
            return reference(payload["task"])
        return {
            "task_id": source,
            "trajectory_id": directory.name,
            "initial_state_hash": "same_clean_source",
            "messages": history(),
        }

    def text(source, directory, prompt, content):
        calls.append((Path(directory).name, prompt, content))
        if Path(directory).name == "abstraction":
            return {
                "tasks": [
                    {
                        "query": "New synthetic goal " + str(i),
                        "action_sequence": "Three actual actions",
                        "completion_criterion": "A precise observation",
                        "evidence_refs": [
                            {k: row[k] for k in ("rollout_id", "action_index")}
                            for row in content["interaction_triplets"][:3]
                        ],
                    }
                    for i in range(2)
                ]
            }
        return {"success": True, "reason": "synthetic"}

    monkeypatch.setattr(e, "execute", execute)
    monkeypatch.setattr(e, "text", text)
    monkeypatch.setattr(e, "quality", lambda *args: {})
    monkeypatch.setattr(
        e,
        "checks",
        lambda *args: [
            {
                "mode": mode,
                "initial_state_hash": "initial",
                "evaluation": {"success": mode == "positive"},
            }
            for mode in ("positive", "noop", "omit_completion", "wrong_answer")
        ],
    )
    e.generate(
        cfg()["feedback_task_ids"][0], {"summary": "weakness"}, tmp_path / "context"
    )
    assert len([x for x in executed if x["mode"] == "exploration"]) == 2
    abstraction = [x for x in calls if x[0] == "abstraction"]
    assert len(abstraction) == 1 and len(e.attempts) == 2
    assert (
        len({r["rollout_id"] for r in abstraction[0][2]["interaction_triplets"]}) == 2
    )
    assert "INTEGRATION OUTPUT CONTRACT" in abstraction[0][1]
    assert len(e.pool) == 2


def test_only_combined_overflow_enables_separate_abstraction(tmp_path, monkeypatch):
    e = engine(tmp_path)
    content = {
        "context": {},
        "output_contract": "at most 2 tasks",
        "interaction_triplets": [
            {"rollout_id": "a", "action_index": 0},
            {"rollout_id": "b", "action_index": 0},
        ],
    }
    calls = []

    def raw(source, directory, prompt, body):
        calls.append(body)
        if Path(directory).name == "abstraction":
            raise ContextTooLong("measured overflow")
        assert len({r["rollout_id"] for r in body["interaction_triplets"]}) == 1
        return {"tasks": [{"query": "one"}]}

    monkeypatch.setattr(e, "raw_text", raw)
    assert (
        len(e.text("source", tmp_path / "abstraction", "prompt", content)["tasks"]) == 2
    )
    assert len(calls) == 3
    assert (
        read_json(tmp_path / "abstraction/fallback.json")["reason"]
        == "measured_combined_input_overflow"
    )
    monkeypatch.setattr(
        e, "raw_text", lambda *a: (_ for _ in ()).throw(ValueError("malformed"))
    )
    with pytest.raises(ValueError, match="malformed"):
        e.text("source", tmp_path / "other/abstraction", "prompt", content)


def test_resume_context_does_not_repeat_teacher_calls_or_lose_acceptance(
    tmp_path, monkeypatch
):
    e = engine(tmp_path)
    signal = event(tmp_path)
    target = tmp_path / "context"
    write_json(target / "s2_context_claim.json", {"event": signal})
    accepted = generated("already_accepted")
    write_json(target / "proposal_0/accepted_task.json", accepted)
    write_json(target / "proposal_0/proposal.json", {"query": "synthetic"})
    monkeypatch.setattr(
        e, "generate", lambda *a: pytest.fail("Must not repeat a dispatched context")
    )
    result = e.run_context(signal, target)
    assert result["interrupted"] and result["accepted"] == [accepted]
    assert e.run_context(signal, target) == result


def test_teacher_budget_reconciles_actual_and_unknown_usage(tmp_path):
    e = engine(tmp_path)
    e.reserve(tmp_path / "text", "text")
    assert e.usage()["unknown_reservations"] == 1
    write_json(
        tmp_path / "text/usage.json",
        {
            "prompt_tokens": 12,
            "generated_tokens": 4,
            "usage_priced_cost": 0.01,
            "wall_seconds": 2,
        },
    )
    e.reserve(tmp_path / "episode", "execute")
    write_json(
        tmp_path / "episode/trajectory.json",
        {
            "usage": {
                "provider_requests": 7,
                "prompt_tokens": 60,
                "generated_tokens": 9,
                "usage_priced_cost": 0.02,
                "wall_seconds": 5,
            }
        },
    )
    usage = e.usage()
    assert (
        usage["requests"] == 8
        and usage["generated_tokens"] == 13
        and usage["seconds"] == 7
    )
    e.cfg["s2"]["teacher_budget"]["requests"] = 8
    with pytest.raises(RuntimeError, match="budget"):
        e.reserve(tmp_path / "extra", "text")


def test_live_evolve_admission_and_24_windows_without_final_extra_batch(
    tmp_path, monkeypatch
):
    from recoveragent.s2_experiment import Experiment
    from recoveragent import s2_evolution

    batches = []

    class FakeEvolution:
        def __init__(self, *args, **kwargs):
            self.pool = []
            batches.append(args[3])

        def usage(self):
            return {"requests": 0}

        def run_context(self, signal, directory):
            task = generated("new_" + str(signal["group_id"]), signal["task_id"])
            return {
                "accepted": [task],
                "candidate_count": 1,
                "explorations": 2,
                "lineage": {
                    "source_task_id": signal["task_id"],
                    "direct_parent_task_id": signal["task_key"],
                },
            }

    monkeypatch.setattr(s2_evolution, "GroupEvolution", FakeEvolution)
    runtime = FakeRuntime(cfg())
    bundle = {
        "pool": [generated("initial")],
        "feedback": [],
        "archive_sha256": "source",
    }
    run = Experiment(
        cfg(),
        bundle,
        {"s0": "S0"},
        tmp_path,
        "evolve",
        runtime,
    )
    from recoveragent.s2_experiment import evaluate

    baseline = evaluate(
        FakeRuntime(cfg()), "S0", cfg()["dev_task_ids"], tmp_path / "baseline"
    )
    state = run.run(baseline)
    assert len(batches) == len(state["feedback_windows"]) == 24
    assert "200" not in state["feedback_windows"] and "160" in state["feedback_windows"]
    assert any(gid and gid.startswith("new_") for _, gid, _, _ in runtime.calls[32:160])
    assert state["optimizer_steps"] == 100
    assert len(runtime.calls) == 920
    assert [t["task_id"] for t in bundle["pool"]] == ["initial"]
    summary = read_json(tmp_path / "summary.json")
    assert summary["generation"]["batches"] == 24
    assert summary["generation"]["candidate_count"] == len(state["lineage"])
