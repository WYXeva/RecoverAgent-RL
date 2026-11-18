"""Synthetic generation/admission only; no model or environment calls."""

import copy
import json
from pathlib import Path

import pytest

from recoveragent import coevolve_light as light, coevolve_prompts as prompts
from recoveragent.io import digest, read_json, write_json
from recoveragent.s2_schedule import GroupFeedback
from recoveragent.v2_runtime import S2_GENERATION_FOCUS_EXECUTION
from test_coevolve_light_llm import judge_reply, reference
from test_s2_evolution import engine
from test_s2_experiment import cfg, make_group

SWEATER = 'Post a question about my last sweater, "Which detergent preserves colors?".'
FRAMES = "Order five identical picture frames, gift wrap them, and ship them home."
SIMPLER = "Order five identical picture frames and ship them home."


def setup_case(tmp_path, monkeypatch, root=SWEATER, parent=None):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GENERATION_FOCUS_EXECUTION)
    e = engine(tmp_path)
    source = cfg()["feedback_task_ids"][0]
    rows = make_group(
        tmp_path,
        "student",
        [True, False, False, False],
        generated_id="parent" if parent is not None else None,
    )
    for row in rows:
        path = Path(row["directory"]) / "history.json"
        value = read_json(path)
        value["messages"][0]["content"] = json.dumps({"instruction": parent or root})
        path.write_text(json.dumps(value))
    original = tmp_path / "original"
    write_json(
        original / "history.json",
        {"messages": [{"role": "user", "content": json.dumps({"instruction": root})}]},
    )
    e.summary["feedback"] = [{"task_id": source, "directory": str(original)}]
    event = GroupFeedback(cfg()["task_evolution"]["signals"]).observe(0, rows)
    return e, source, e.context(event, tmp_path / "context")


def run_candidates(e, source, feedback, directory, queries, monkeypatch):
    calls, solved = [], []

    def text(task_id, path, prompt, content, *, role=None):
        calls.append((prompt, copy.deepcopy(content)))
        if prompt == prompts.GENERATION:
            return {
                "tasks": [
                    {
                        "query": query,
                        "action_sequence": "Synthetic feasible steps.",
                        "completion_criterion": "Fulfill the public query.",
                    }
                    for query in queries
                ]
            }
        assert prompt == prompts.JUDGE
        return judge_reply()

    def execute(task_id, path, payload):
        assert (
            payload["mode"] == "validation"
        )  # Existing observation skips exploration.
        solved.append(payload["task"]["instruction"])
        result = reference(payload["task"])
        write_json(path / "trajectory.json", result)
        return result

    monkeypatch.setattr(e, "raw_text", text)
    monkeypatch.setattr(e, "execute", execute)
    light.generate(e, source, feedback, directory)
    return calls, solved


def test_actual_abstraction_gets_root_parent_same_source_and_four_histories(
    tmp_path, monkeypatch
):
    e, source, feedback = setup_case(tmp_path, monkeypatch, parent=FRAMES)
    e.pool = [
        {
            "task_id": "parent",
            "source_task_id": source,
            "instruction": FRAMES,
            "validation": {"secret": "must not be supplied"},
        },
        {
            "task_id": "other",
            "source_task_id": cfg()["feedback_task_ids"][1],
            "instruction": "Unrelated environment",
        },
    ]
    before = copy.deepcopy(feedback)
    calls, solved = run_candidates(
        e, source, feedback, tmp_path / "out", [], monkeypatch
    )
    assert not solved and len(calls) == 1 and calls[0][0] == prompts.GENERATION
    content = calls[0][1]
    assert content["feedback"] == before == feedback
    assert len(content["feedback"]["trajectories"]) == 4
    assert content["feedback"]["group"]["outcomes"] == [True, False, False, False]
    assert content["task_comparison"] == {
        "root_task": {"task_id": source, "query": SWEATER},
        "parent_task": {"task_id": "parent", "query": FRAMES},
        "same_source_tasks": [{"task_id": "parent", "query": FRAMES}],
    }
    assert "must not be supplied" not in json.dumps(content)
    assert "Unrelated environment" not in json.dumps(content)


@pytest.mark.parametrize(
    "query,kind",
    [(SWEATER, "root_task"), (SWEATER[:-1], "root_task"), (FRAMES, "parent_task")],
)
def test_root_and_parent_copies_stop_before_solver(tmp_path, monkeypatch, query, kind):
    e, source, feedback = setup_case(tmp_path, monkeypatch, parent=FRAMES)
    calls, solved = run_candidates(
        e, source, feedback, tmp_path / "out", [query], monkeypatch
    )
    assert not solved and len(calls) == 1 and not e.pool
    assert e.attempts[0]["quality_status"] == "duplicate_existing"
    assert e.attempts[0]["duplicate_of"]["kind"] == kind


def test_real_constraint_change_passes_and_duplicate_within_batch_does_not(
    tmp_path, monkeypatch
):
    e, source, feedback = setup_case(tmp_path, monkeypatch, root=FRAMES)
    calls, solved = run_candidates(
        e, source, feedback, tmp_path / "out", [SIMPLER, SIMPLER], monkeypatch
    )
    assert solved == [SIMPLER]
    assert [p for p, _ in calls] == [prompts.GENERATION, prompts.JUDGE]
    assert len(e.pool) == 1 and e.attempts[0]["accepted"]
    assert e.attempts[1]["duplicate_of"]["task_id"] == e.pool[0]["task_id"]
    assert e.attempts[1]["quality_status"] == "duplicate_existing"
    saved = read_json(Path(e.attempts[0]["directory"]) / "proposal.json")
    assert set(saved) == {"query", "action_sequence", "completion_criterion"}


@pytest.mark.parametrize(
    "a,b",
    [
        ('Post "Which detergent?".', 'Post "Which detergent!".'),
        ('Reply "OK".', 'Reply "ok".'),
        ('Reply "A  B".', 'Reply "A B".'),
        ('Reply "Done."', 'Reply "Done"'),
        ("Report 3.14", "Report 314"),
        ("Report ID 001", "Report ID 1"),
        ("Do not send the email.", "Do send the email."),
        ("Process one song from the file.", "Process two songs from the file."),
    ],
)
def test_simple_rule_preserves_meaningful_text(a, b):
    assert light.duplicate_key(a) != light.duplicate_key(b)


def test_new_profile_only_changes_generation_metadata_and_keeps_old_lock(tmp_path):
    from recoveragent.v2_runtime import S2_RUNTIME, verify_lock

    old = read_json("configs/v2_s2_judge_reference_20261117.json")
    new = read_json(f"configs/{S2_GENERATION_FOCUS_EXECUTION}.json")
    assert {
        k: v
        for k, v in new.items()
        if k
        not in {
            "revision",
            "authorization",
            "generation_input_revision",
            "generation_prompt_sha256",
        }
    } == {k: v for k, v in old.items() if k not in {"revision", "authorization"}}
    assert new["generation_prompt_sha256"] == digest(prompts.GENERATION)
    # Use explicit protocol choices rather than silently accepting a changed old lock.
    from unittest.mock import patch

    with patch.dict(
        "os.environ", {"RECOVERAGENT_S2_EXECUTION": S2_GENERATION_FOCUS_EXECUTION}
    ):
        with pytest.raises(ValueError, match="source lock|source changed"):
            verify_lock(S2_RUNTIME)
    with patch.dict("os.environ", {"RECOVERAGENT_S2_EXECUTION": old["revision"]}):
        with pytest.raises(ValueError, match="source lock|source changed"):
            verify_lock(S2_RUNTIME)
