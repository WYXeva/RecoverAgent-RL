"""Real Teacher validation and synthetic execution: no provider or GPU calls."""

from pathlib import Path

import pytest

from recoveragent.io import read_json, write_json
from recoveragent.p3a import validate_config
from recoveragent.v2_runtime import S2_GENERATION_EXECUTION, S2_RUNTIME
from recoveragent.v2_teacher import configuration
from test_s2_evolution import engine
from test_s2_experiment import cfg, FakeRuntime, generated


@pytest.fixture
def execution(monkeypatch):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GENERATION_EXECUTION)


def test_real_teacher_entry_accepts_s2_and_rejects_tampering(execution):
    config = cfg()
    worker = configuration(
        config["feedback_task_ids"][0],
        config["models"][config["selected_model"]],
        "synthetic",
        100,
        recovery=False,
        runtime=S2_RUNTIME,
    )
    validate_config(worker)
    worker["teacher"]["temperature"] += 0.1
    with pytest.raises(ValueError, match="configuration changed"):
        validate_config(worker)
    worker["v2_runtime"] = "unregistered"
    with pytest.raises(ValueError, match="Unknown learning runtime"):
        validate_config(worker)


def test_execute_reservation_does_not_create_teacher_output(tmp_path, execution):
    e = engine(tmp_path)
    e.identity = e.cfg["models"][e.cfg["selected_model"]]
    e.deployment["worker_python"] = "synthetic"
    called = []

    def isolated(python, command, payload, destination, deadline):
        validate_config(payload["config"])
        target = Path(payload["output"])
        assert not target.exists()
        target.mkdir(exist_ok=False)  # production run_teacher_episode contract
        called.append(target)
        write_json(
            target / "trajectory.json",
            {
                "status": "valid",
                "usage": {
                    "provider_requests": 1,
                    "prompt_tokens": 3,
                    "generated_tokens": 4,
                    "usage_priced_cost": 0.001,
                    "wall_seconds": 2,
                },
            },
        )

    e.isolated = isolated
    target = tmp_path / "context_explore_0"
    e.execute(e.cfg["feedback_task_ids"][0], target, {"mode": "exploration"})
    assert called == [target]
    assert (tmp_path / "context_explore_0.budget/s2_operation.json").exists()
    assert e.usage()["requests"] == 1 and e.usage()["unknown_reservations"] == 0


def test_explicit_price_opt_out_keeps_other_caps_and_unknown_reservations(
    tmp_path, execution
):
    e = engine(tmp_path)
    e.cfg["s2"]["teacher_budget"]["priced_cost"] = 0.1
    e.reserve(tmp_path / "a", "execute")
    e.reserve(tmp_path / "b", "execute")
    assert e.usage()["unknown_reservations"] == 2
    assert e.usage()["priced_cost"] > 0.1
    assert e.remaining() == 0.1
    e.cfg["s2"]["teacher_budget"]["requests"] = 160
    with pytest.raises(RuntimeError, match="budget"):
        e.reserve(tmp_path / "c", "text")


def test_summary_global_indices_are_supplied_and_invalid_refs_still_rejected(
    tmp_path, monkeypatch
):
    e = engine(tmp_path)
    messages = [{"role": "assistant", "content": "observed"} for _ in range(3)]
    calls = []

    def text(source, directory, prompt, content):
        calls.append(content)
        assert "GLOBAL zero-based" in prompt
        assert [r["message_index"] for r in content["messages"]] == [54, 55, 56]
        assert [r["message"] for r in content["messages"]] == messages
        return {"summary": "Observation", "evidence_refs": [{"message_index": 55}]}

    monkeypatch.setattr(e, "text", text)
    result = e.summarize_history("source", tmp_path / "summary", {}, messages, 54)
    assert result[0]["message_start"] == 54
    monkeypatch.setattr(
        e,
        "text",
        lambda *a: {"summary": "Observation", "evidence_refs": [{"message_index": 1}]},
    )
    with pytest.raises(ValueError, match="Untraceable"):
        e.summarize_history("source", tmp_path / "bad", {}, messages, 54)
    assert messages == [{"role": "assistant", "content": "observed"}] * 3


def test_regeneration_preserves_failed_batch_and_admits_into_next_slots(
    tmp_path, monkeypatch
):
    from recoveragent.s2_experiment import Experiment
    from recoveragent import s2_evolution
    from recoveragent.s2_reporting import report

    calls = []
    accepting = False

    class FakeEvolution:
        def __init__(self, *args, **kwargs):
            self.pool = []

        def usage(self):
            return {"requests": len(calls)}

        def run_context(self, event, directory):
            calls.append(event)
            return {
                "accepted": [
                    generated("retry_new_" + str(event["group_id"]), event["task_id"])
                ]
                if accepting
                else [],
                "candidate_count": int(accepting),
                "explorations": 2 if accepting else 0,
                "context_completed": True,
                "lineage": {
                    "source_task_id": event["task_id"],
                    "direct_parent_task_id": event["task_key"],
                },
            }

    monkeypatch.setattr(s2_evolution, "GroupEvolution", FakeEvolution)
    runtime = FakeRuntime(cfg())
    run = Experiment(
        cfg(),
        {"pool": [generated("initial")], "feedback": [], "archive_sha256": "source"},
        {"s0": "S0"},
        tmp_path,
        "evolve",
        runtime,
    )
    for _ in range(8):
        run.group()
        run.update()
    run.feedback()
    original = Path(run.state["feedback_windows"]["8"])
    before = original.read_bytes()
    selected = read_json(original)["selected"]
    actor, optimizer_steps = run.state["actor"], run.state["optimizer_steps"]
    accepting = True
    run.feedback(retry_window=8, retry_id="user_001")
    assert calls[len(selected) :] == selected
    assert original.read_bytes() == before
    assert (
        run.state["actor"] == actor and run.state["optimizer_steps"] == optimizer_steps
    )
    assert (
        len(run.state["feedback_windows"])
        == len(run.state["feedback_regenerations"])
        == 1
    )
    count = len(calls)
    run.feedback(retry_window=8, retry_id="user_001")
    assert len(calls) == count
    with pytest.raises(ValueError, match="already produced"):
        run.feedback(retry_window=8, retry_id="user_002")
    run.group()
    run.group()
    assert any(
        gid and gid.startswith("retry_new_") for _, gid, _, _ in runtime.calls[-4:]
    )
    report(tmp_path, run.state, [])
    summary = read_json(tmp_path / "summary.json")
    assert summary["generation"]["batches"] == 1
    assert summary["generation"]["regeneration_batches"] == 1
