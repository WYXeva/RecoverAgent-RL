"""CPU/synthetic integration only: no model calls, AppWorld episodes or training."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from recoveragent import (
    coevolve_llm as llm,
    coevolve_tasks,
    coevolve_prompts as prompts,
)
from recoveragent.io import digest, read_json, write_json
from recoveragent.s2_reward import finalize
from recoveragent.s2_schedule import GroupFeedback
from recoveragent.v2_grpo import group_advantages
from recoveragent.v2_runtime import (
    S2_GENERATION_FOCUS_EXECUTION,
    S2_GOAL_REPAIR_EXECUTION,
)
from test_s2_evolution import engine
from test_s2_experiment import cfg, make_group, FakeRuntime
from test_coevolve import history


@pytest.fixture
def lightweight(monkeypatch):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GENERATION_FOCUS_EXECUTION)


def proposal():
    return {
        "query": "Count the unread inbox messages; report their count.",
        "action_sequence": "Query the unread inbox count.",
        "completion_criterion": "Report the requested count.",
    }


def new_task():
    source = cfg()["feedback_task_ids"][0]
    return coevolve_tasks.candidate(
        proposal(),
        [
            {
                "task_id": source,
                "trajectory_id": "one",
                "messages": history(("gmail__show_emails",)),
            }
        ],
        {},
        scoring_revision=llm.REVISION,
    )


def reference(task):
    return {
        "task_id": task["source_task_id"],
        "trajectory_id": "solver",
        "status": "valid",
        "initial_state_hash": "clean",
        "messages": history(("gmail__show_emails",)),
        "coevolve_terminal": {
            "instruction": task["instruction"],
            "status": "success",
            "answer": 0,
        },
    }


def judge_reply(success=True):
    return {
        "success": success,
        "reason": "Synthetic test judgment, not a model result",
        "evidence_steps": [0],
    }


def admitted_task(task, tmp_path):
    saved = tmp_path / "solver.json"
    write_json(saved, reference(task))
    return llm.admit(
        SimpleNamespace(text=lambda *a, **kw: judge_reply()),
        task,
        tmp_path / "admitted",
        reference_path=saved,
    )


def test_three_fields_single_action_and_loading():
    task = new_task()
    coevolve_tasks.validate_task(task, task["source_task_id"], require_validation=False)
    assert "evidence_refs" not in task and "success_conditions" not in task
    with pytest.raises(ValueError, match="LLM admission"):
        coevolve_tasks.validate_task(task, task["source_task_id"])
    assert coevolve_tasks.validation_hint(task) == prompts.SOLVER
    assert "proposed_solution" not in coevolve_tasks.validation_hint(task)


@pytest.mark.parametrize(
    "empty,explore", [(False, False), (True, False), (False, True)]
)
def test_actual_generation_prompts_no_old_gates_and_optional_one_exploration(
    tmp_path, monkeypatch, lightweight, empty, explore
):
    e = engine(tmp_path)
    group = make_group(tmp_path, "feedback", [False, True, False, False])
    if explore:
        for row in group:
            write_path = Path(row["directory"]) / "history.json"
            saved = read_json(write_path)
            saved["messages"] = saved["messages"][:1]
            write_path.write_text(json.dumps(saved))
    event = GroupFeedback(cfg()["task_evolution"]["signals"]).observe(0, group)
    textual, executed = [], []

    def text(source, directory, prompt, content, *, role=None):
        textual.append((prompt, content))
        if prompt == prompts.GENERATION:
            assert len(content["feedback"]["trajectories"]) == 4
            assert content["feedback"]["group"]["outcomes"] == [
                False,
                True,
                False,
                False,
            ]
            assert "success_conditions" not in prompt and "XML" not in prompt
            assert "evidence_refs" not in prompt and "three" not in prompt
            return {"tasks": [] if empty else [proposal()]}
        assert prompt == prompts.JUDGE
        assert set(content) == {"query", "reference", "execution", "final_submission"}
        assert content["reference"] is None
        return judge_reply()

    def execute(source, directory, payload):
        executed.append(payload)
        if payload["mode"] == "validation":
            assert coevolve_tasks.validation_hint(payload["task"]) == prompts.SOLVER
            result = reference(payload["task"])
        else:
            visible = coevolve_tasks.exploration_instruction(payload)
            assert visible.startswith(prompts.EXPLORATION)
            assert "at least" not in visible and "three" not in visible
            result = {
                "task_id": source,
                "trajectory_id": "explore",
                "messages": history(("gmail__show_emails",)),
            }
        write_json(directory / "trajectory.json", result)
        return result

    monkeypatch.setattr(e, "raw_text", text)
    monkeypatch.setattr(e, "execute", execute)
    for forbidden in ("quality", "checks", "admit_proposal", "semantic_judgment"):
        monkeypatch.setattr(
            e, forbidden, lambda *a, **kw: pytest.fail("legacy gate invoked")
        )
    context = e.context(event, tmp_path / "context")
    assert textual == []
    e.generate(event["task_id"], context, tmp_path / "generated")
    assert [p for p, _ in textual] == (
        [prompts.GENERATION] if empty else [prompts.GENERATION, prompts.JUDGE]
    )
    assert sum(p["mode"] == "exploration" for p in executed) == int(explore)
    assert sum(p["mode"] == "validation" for p in executed) == int(not empty)
    assert len(e.pool) == int(not empty)
    if not empty:
        coevolve_tasks.validate_task(e.pool[0], event["task_id"])


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"success": True},
        {"success": 1, "reason": "x", "evidence_steps": []},
        {"success": False, "reason": "x", "evidence_steps": [True]},
        {"success": True, "reason": "x", "evidence_steps": [77]},
    ],
)
def test_bad_judge_structure_invalid_not_failure(value):
    task = new_task()
    evaluation = llm.reference_evaluation(task, reference(task))
    result = finalize({"evaluation": evaluation, "fault": None}, lambda _: value)
    assert result["success"] is None and not result["reward_valid"]
    assert result["classification"] == "scoring_gap"


def test_failed_judge_call_invalid_and_business_false_valid():
    task = new_task()
    evaluation = llm.reference_evaluation(task, reference(task))
    failed = finalize(
        {"evaluation": evaluation, "fault": None}, lambda _: judge_reply(False)
    )
    assert failed["success"] is False and failed["reward_valid"]
    invalid = finalize(
        {"evaluation": evaluation, "fault": None},
        lambda _: (_ for _ in ()).throw(RuntimeError("synthetic transport failure")),
    )
    assert invalid["success"] is None and not invalid["reward_valid"]
    official = {
        "evaluation": {"status": "valid", "success": False},
        "success": False,
        "reward_valid": True,
    }
    assert (
        finalize(official, lambda _: pytest.fail("official score must not call Judge"))
        == official
    )


def test_solver_worker_crash_is_deferred_not_business_failure(
    tmp_path, monkeypatch, lightweight
):
    import subprocess

    e = engine(tmp_path)
    monkeypatch.setattr(e, "raw_text", lambda *a: {"tasks": [proposal()]})
    monkeypatch.setattr(
        e,
        "execute",
        lambda *a: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "worker")),
    )
    group = make_group(tmp_path, "feedback", [False] * 4)
    event = GroupFeedback(cfg()["task_evolution"]["signals"]).observe(0, group)
    e.generate(
        event["task_id"],
        e.context(event, tmp_path / "context"),
        tmp_path / "generation",
    )
    assert not e.pool and len(e.attempts) == 1
    assert e.attempts[0]["quality_status"] == "deferred_judge"
    assert "infrastructure" in e.attempts[0]["reason"]


def test_shared_judge_admission_reward_and_synthetic_update_resume(
    tmp_path, monkeypatch, lightweight
):
    from recoveragent.s2_experiment import Experiment
    from recoveragent import s2_reward
    from recoveragent.s2_backend import BackendRuntime
    from recoveragent.s2_evolution import GroupEvolution

    calls = []

    def raw_text(self, source, directory, prompt, content, *, role=None):
        calls.append((prompt, content))
        assert prompt == prompts.JUDGE
        assert "expected_answer" not in content and "success_conditions" not in content
        return judge_reply(content["final_submission"]["answer"] == 0)

    monkeypatch.setattr(GroupEvolution, "raw_text", raw_text)
    e = engine(tmp_path / "teacher")
    task = new_task()
    saved = tmp_path / "solver.json"
    write_json(saved, reference(task))
    before = saved.read_bytes()
    accepted = llm.admit(
        e, task, tmp_path / "admission", reference_path=saved, expected_initial="clean"
    )
    assert saved.read_bytes() == before
    # Common scorer path also finalizes each generated Student episode.
    runtime_cfg = cfg()
    backend = BackendRuntime(runtime_cfg, {"deadline_epoch": 1e20}, tmp_path / "reward")
    rows = make_group(
        tmp_path, "student", [False] * 4, generated_id=accepted["task_id"]
    )
    for i, row in enumerate(rows):
        pending = llm.pending(
            accepted,
            read_json(Path(row["directory"]) / "history.json")["messages"],
            {"status": "success", "answer": i % 2},
            initial_state_hash="clean",
        )
        result = {
            **row,
            "fault": None,
            "evaluation": pending,
            "success": None,
            "reward_valid": False,
        }
        scored = s2_reward.score(backend, result, row["directory"])
        rows[i] = scored
    assert [r["success"] for r in rows] == [True, False, True, False]
    assert all(prompt == prompts.JUDGE for prompt, _ in calls) and len(calls) == 5
    assert group_advantages(rows) and all(r["reward_valid"] for r in rows)
    feedback = GroupFeedback(runtime_cfg["task_evolution"]["signals"])
    assert feedback.observe(0, rows)["complete"]
    bad = copy.deepcopy(rows)
    bad[1].update(success=None, reward_valid=False, classification="scoring_gap")
    assert group_advantages(bad) is None
    assert not feedback.observe(1, bad)["complete"]

    class Runtime(FakeRuntime):
        def prepare_rollouts(self, actor):
            pass

        def rollout(self, actor, chosen, directory, seed, **kw):
            result = super().rollout(actor, chosen, directory, seed, **kw)
            if chosen.get("generated_task"):
                observed = read_json(Path(directory) / "history.json")["messages"]
                result = finalize(
                    {
                        **result,
                        "fault": None,
                        "evaluation": llm.pending(
                            chosen["generated_task"],
                            observed,
                            {"status": "success", "answer": seed % 2},
                            initial_state_hash="clean",
                        ),
                    },
                    lambda _: judge_reply(bool(seed % 2)),
                )
            return result

        def update(self, request, directory):
            assert all(
                all(r.get("reward_valid", True) for r in g) for g in request["groups"]
            )
            return super().update(request, directory)  # Simulated update receipt only.

    runtime = Runtime(runtime_cfg)
    bundle = {
        "scoring_revision": llm.REVISION,
        "pool": [accepted],
        "archive_sha256": "synthetic",
        "feedback": [],
    }
    run = Experiment(
        runtime_cfg, bundle, {"s0": "S0"}, tmp_path / "run", "static", runtime
    )
    run.group()
    run.group()
    run.update()
    assert run.state["optimizer_steps"] == 1
    resumed = Experiment(
        runtime_cfg, bundle, {"s0": "S0"}, tmp_path / "run", "static", runtime
    )
    resumed.update()
    assert digest(resumed.state) == digest(run.state) and len(runtime.calls) == 8
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GOAL_REPAIR_EXECUTION)
    with pytest.raises(ValueError, match="protocols"):
        Experiment(
            runtime_cfg, bundle, {"s0": "S0"}, tmp_path / "run", "static", runtime
        )


def test_explicit_llm_evaluator_never_falls_back_to_hash_or_exact_answer(
    monkeypatch, tmp_path, lightweight
):
    task = admitted_task(new_task(), tmp_path)
    adapter = SimpleNamespace(
        world=SimpleNamespace(
            requester=SimpleNamespace(
                request=lambda *a, **kw: {
                    "instruction": task["instruction"],
                    "status": "success",
                    "answer": 0,
                }
            )
        )
    )
    monkeypatch.setattr(
        coevolve_tasks, "business_hash", lambda *_: pytest.fail("old hash scorer")
    )
    monkeypatch.setattr(
        coevolve_tasks, "answer_matches", lambda *_: pytest.fail("old answer scorer")
    )
    result = coevolve_tasks.evaluate(
        adapter, task, reference(task)["messages"], initial_state_hash="clean"
    )
    assert result["evaluator"] == llm.REVISION and result["status"] == "needs_llm_judge"


def test_initial_common_pool_reuses_saved_solver_and_missing_stays_pending(
    tmp_path, monkeypatch, lightweight
):
    from recoveragent import s2_inputs, s2_llm_pool
    from recoveragent.s2_experiment import require_pool_protocol

    task = new_task()
    old = copy.deepcopy(task)
    old.pop("scoring_revision")
    ref = reference(old)
    old["execution_contract"] = {
        "reference_trajectory_sha256": digest(ref),
        "initial_state_hash": "clean",
    }
    old["task_id"] = "historical_1"
    missing = {
        **copy.deepcopy(old),
        "task_id": "historical_2",
        "instruction": "A different missing reference task",
    }
    legacy = {
        "pool": [old, missing],
        "archive_sha256": "synthetic_archive",
        "feedback": [],
    }
    monkeypatch.setattr(s2_inputs, "load_bundle", lambda _: legacy)
    legacy_dir = tmp_path / "legacy"
    write_json(legacy_dir / "manifest.json", {"revision": "synthetic_archive"})
    ref_path = tmp_path / "original.json"
    write_json(ref_path, ref)
    refs = tmp_path / "references.json"
    write_json(refs, {"historical_1": str(ref_path)})
    prepared = s2_llm_pool.prepare_pool(legacy_dir, tmp_path / "prepared", refs)
    assert [r["status"] for r in prepared["candidates"]] == [
        "ready_for_one_judge",
        "deferred_validation",
    ]
    e = engine(tmp_path / "transport")
    calls = []
    monkeypatch.setattr(e, "execute", lambda *a, **kw: pytest.fail("no Solver rerun"))
    monkeypatch.setattr(
        e,
        "raw_text",
        lambda source, directory, prompt, content, **kw: calls.append((prompt, content))
        or judge_reply(),
    )
    bundle = s2_llm_pool.admit_pool(
        cfg(), {}, tmp_path / "prepared", tmp_path / "pool", engine=e
    )
    assert len(bundle["pool"]) == 1 and bundle["solver_reruns"] == 0
    require_pool_protocol(bundle)
    assert calls[0][0] == prompts.JUDGE and len(calls) == 1
    assert (
        s2_llm_pool.admit_pool(
            cfg(), {}, tmp_path / "prepared", tmp_path / "pool", engine=e
        )
        == bundle
    )
    assert len(calls) == 1 and read_json(ref_path) == ref
    with pytest.raises(ValueError, match="common initial pool"):
        require_pool_protocol(legacy)


def test_no_legacy_prompt_in_actual_teacher_text_request():
    from recoveragent.teacher import TeacherPolicy

    policy = object.__new__(TeacherPolicy)
    policy.config = {
        "model": "synthetic",
        "temperature": 0.5,
        "max_request_bytes": 100000,
    }
    for prompt in (prompts.GENERATION, prompts.JUDGE):
        request = policy.build_text_request(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "synthetic public evidence"},
            ],
            64,
        )
        assert request["messages"][0]["content"] == prompt
        assert len(request["messages"]) == 2
        assert "INTEGRATION OUTPUT CONTRACT" not in str(request)
        assert "success_conditions" not in str(request) and "<tasks>" not in str(
            request
        )


def test_formal_limits_use_lightweight_settings(lightweight):
    from recoveragent.s2_experiment import configuration
    from recoveragent.v2_runtime import s2_execution

    config = configuration()
    settings = s2_execution()
    assert config["task_evolution"]["explorations_per_context"] == 1
    assert config["task_evolution"]["proposals_per_context"] == 2
    assert config["task_evolution"]["max_proposals"] == 192
    assert config["s2"]["student_trajectories_per_branch"] == 800
    assert settings["stop_after_round"] == 1
    assert settings["reward_judge_budget"]["requests"] == 80
    assert settings["fresh16_judge_budget"]["requests"] == 16
    assert settings["initial_pool_judge_budget"]["requests"] == 9
    assert not settings["review_generated_tasks"]
    assert not settings["curriculum_admission"]


def test_obvious_duplicate_does_not_rerun_solver_or_remove_old_task(
    tmp_path, monkeypatch, lightweight
):
    e = engine(tmp_path)
    old = new_task()
    e.pool.append(old)
    calls = []
    monkeypatch.setattr(
        e,
        "raw_text",
        lambda *a: calls.append(a[2]) or {"tasks": [proposal()]},
    )
    monkeypatch.setattr(e, "execute", lambda *a: pytest.fail("duplicate rerun"))
    group = make_group(tmp_path, "feedback", [False] * 4)
    event = GroupFeedback(cfg()["task_evolution"]["signals"]).observe(0, group)
    feedback = e.context(event, tmp_path / "context")
    e.generate(event["task_id"], feedback, tmp_path / "generation")
    assert calls == [prompts.GENERATION]
    assert e.pool == [old]
    assert e.attempts[0]["quality_status"] == "duplicate_existing"


def test_historical_check_keeps_labels_out_of_judge_and_resumes(
    tmp_path, monkeypatch, lightweight
):
    import time
    from recoveragent import s2_llm_pool, s2_evolution, v2_tasks

    task = new_task()
    evaluation = llm.reference_evaluation(task, reference(task))
    plan = {
        "cases": [
            {
                "row": i,
                "source_task_id": task["source_task_id"],
                "evaluation": evaluation,
                "expected_human_review": i % 2 == 0,
            }
            for i in range(4)
        ]
    }
    write_json(tmp_path / "plan.json", plan)
    write_json(
        tmp_path / "configs/s2_llm_judge_check_20261117.json",
        {"plan_sha256": digest(plan)},
    )
    e = engine(tmp_path / "engine")
    calls = []

    def text(source, directory, prompt, content, *, role=None):
        assert prompt == prompts.JUDGE
        assert content == evaluation["judge_input"]
        assert "expected_human_review" not in content
        calls.append(content)
        return judge_reply()

    monkeypatch.setattr(e, "raw_text", text)
    monkeypatch.setattr(s2_evolution, "GroupEvolution", lambda *a, **kw: e)
    monkeypatch.setattr(v2_tasks, "ROOT", tmp_path)
    deploy = {"deadline_epoch": time.time() + 1000}
    first = s2_llm_pool.check_judge(
        cfg(), deploy, tmp_path / "plan.json", tmp_path / "check"
    )
    assert len(calls) == 4
    assert [r["matches_review"] for r in first["results"]] == [True, False, True, False]
    assert first["new_rollouts"] == first["training_updates"] == 0
    assert (
        s2_llm_pool.check_judge(
            cfg(), deploy, tmp_path / "plan.json", tmp_path / "check"
        )
        == first
    )
    assert len(calls) == 4


@pytest.mark.parametrize("generated", [False, True])
def test_real_episode_route_keeps_official_original_and_llm_generated(
    tmp_path, monkeypatch, lightweight, generated
):
    from recoveragent import v2_episode, v2_generator
    from recoveragent.v2_runtime import S2_RUNTIME
    import json

    task = new_task()
    e = engine(tmp_path / "teacher")
    monkeypatch.setattr(e, "raw_text", lambda *a, **kw: judge_reply())
    saved = tmp_path / "reference.json"
    write_json(saved, reference(task))
    accepted = llm.admit(e, task, tmp_path / "accepted", reference_path=saved)
    official_calls = []

    class Adapter:
        def __init__(self, *a, **kw):
            self.instruction = "An original official task"
            self.world = SimpleNamespace(
                requester=SimpleNamespace(
                    request=lambda *a, **kw: {
                        "instruction": self.instruction,
                        "status": "success",
                        "answer": 0,
                    }
                ),
                task_completed=lambda: True,
            )

        def configure_schema_compatibility(self, *a):
            pass

        def public_task(self):
            return {"instruction": self.instruction}

        def fingerprint(self):
            return "clean"

        def initial_tools(self):
            return []

        def execute(self, *a):
            return [], []

        def save_terminal(self):
            pass

        def close(self):
            pass

    class Session:
        def __init__(self, adapter, tokenizer, messages, tools, **kwargs):
            assert "action_sequence" not in str(
                messages
            ) and "completion_criterion" not in str(messages)
            assert "scoring_revision" not in str(messages)
            self.messages = messages + history(("gmail__show_emails",))
            self.tools = tools
            self.stopped = True
            self.reward_fault = None
            self.identity = {"checkpoint_id": "S0"}
            self.usage = {"model_requests": 1, "generated_tokens": 3}
            self.termination = "completion_claim"

    monkeypatch.setattr(v2_episode, "AppWorldAdapter", Adapter)
    monkeypatch.setattr(v2_episode, "StudentSession", Session)
    monkeypatch.setattr(v2_episode, "load_tokenizer", lambda _: None)
    monkeypatch.setattr(v2_episode, "environment_check", lambda: None)
    monkeypatch.setattr(
        v2_generator,
        "apply_instruction",
        lambda adapter, instruction: setattr(adapter, "instruction", instruction),
    )
    monkeypatch.setattr(
        v2_episode,
        "evaluate_isolated",
        lambda *a, **kw: official_calls.append(a[0])
        or {"status": "valid", "success": False},
    )
    monkeypatch.setattr(
        coevolve_tasks, "business_hash", lambda *_: pytest.fail("legacy scorer used")
    )
    task_id = task["source_task_id"] if generated else cfg()["dev_task_ids"][0]
    request = {
        "task_id": task_id,
        "output": str(tmp_path / "episode"),
        "purpose": "grpo" if generated else "evaluation",
        "appworld_root": "synthetic",
        "execution_protocol": S2_RUNTIME,
        "completion_contract_revision": "completion_submission_v2",
        "generated_scoring_revision": llm.REVISION,
        "checkpoint_id": "S0",
        "model": {},
        "student_limits": {},
    }
    if generated:
        request["generated_task"] = accepted
    result = v2_episode.run(request, lambda _: pytest.fail("no actual model"))
    assert result["fault"] is None
    if generated:
        assert (
            official_calls == [] and result["evaluation"]["status"] == "needs_llm_judge"
        )
        assert finalize(result, lambda _: judge_reply())["success"] is True
    else:
        assert (
            official_calls == [task_id]
            and result["success"] is False
            and result["reward_valid"]
        )
    assert json.loads((tmp_path / "episode/history.json").read_text())["messages"]
