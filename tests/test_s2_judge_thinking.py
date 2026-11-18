"""Real Judge request/parse/reservation chain; only network responses are synthetic."""

import copy
import json

import pytest

from recoveragent import coevolve_llm as llm, v2_teacher, v2_runtime
from recoveragent.coevolve_prompts import JUDGE
from recoveragent.io import digest, read_json
from recoveragent.s2_experiment import configuration
from recoveragent.s2_reward import finalize
from recoveragent.teacher import TeacherPolicy
from test_coevolve_light_llm import new_task, reference, admitted_task
from test_s2_teacher_context import text_engine


@pytest.fixture
def setup(monkeypatch, tmp_path):
    from pathlib import Path

    monkeypatch.setenv(
        "RECOVERAGENT_S2_EXECUTION", v2_runtime.S2_GENERATION_FOCUS_EXECUTION
    )
    local = Path("runs/s2_context_validation_20261117/teacher_tokenizer")
    if (local / "tokenizer.json").exists():
        monkeypatch.setenv("RECOVERAGENT_TEACHER_TOKENIZER_DIR", str(local.resolve()))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-only")
    cfg = configuration()
    task = new_task()
    pending = llm.reference_evaluation(task, reference(task))
    return text_engine(cfg, tmp_path), task, pending


def mock_transport(monkeypatch, change=None):
    requests = []

    def post(self, request):
        requests.append(copy.deepcopy(request))
        response = {
            "model": request["model"],
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "reasoning_content": 'Synthetic reasoning, not final JSON: {"success": true}',
                        "content": json.dumps(
                            {
                                "reason": "Synthetic business failure",
                                "evidence_steps": [0],
                                "success": False,
                            }
                        ),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 150,
                "completion_tokens_details": {"reasoning_tokens": 100},
            },
        }
        if change:
            change(response)
        return response

    monkeypatch.setattr(TeacherPolicy, "_post_session", post)
    return requests


def test_admission_and_reward_share_actual_thinking_request(
    setup, tmp_path, monkeypatch
):
    engine, task, pending = setup
    requests = mock_transport(monkeypatch)
    accepted = admitted_task(task, tmp_path / "fixture")
    student_pending = llm.pending(
        accepted,
        reference(task)["messages"],
        {"status": "success", "answer": 0},
        initial_state_hash="clean",
    )
    results = []
    for purpose in ("admission", "student_reward"):
        observed = pending if purpose == "admission" else student_pending
        target = tmp_path / purpose
        results.append(
            finalize(
                {"evaluation": observed},
                lambda _: llm.judge(
                    engine, task["source_task_id"], observed, target, purpose=purpose
                ),
            )
        )
        binding = read_json(target / "judge_binding.json")
        assert binding["call_identity"] == llm.call_identity()
        receipt = read_json(target / "request/call_receipt.json")
        assert receipt["reasoning_effort"] == "high"
        assert receipt["prompt_sha256"] == digest(JUDGE)
        assert (
            read_json(target / "request/context.json")["reserved_output_tokens"] == 8192
        )
        assert (
            read_json(target / "request/s2_operation.json")["reserve"][
                "generated_tokens"
            ]
            == 8192
        )
        assert (
            "reasoning_content"
            in read_json(target / "request/response.json")["choices"][0]["message"]
        )
    assert {k: v for k, v in requests[0].items() if k != "messages"} == {
        k: v for k, v in requests[1].items() if k != "messages"
    }
    assert requests[0]["messages"][0] == requests[1]["messages"][0]
    assert pending["judge_input"]["reference"] is None
    assert student_pending["judge_input"]["reference"] is not None
    assert json.loads(requests[0]["messages"][-1]["content"])["reference"] is None
    assert (
        json.loads(requests[1]["messages"][-1]["content"])["reference"]
        == (student_pending["judge_input"]["reference"])
    )
    assert requests[0]["thinking"] == {"type": "enabled"}
    assert requests[0]["reasoning_effort"] == "high"
    assert requests[0]["max_tokens"] == 8192
    assert "temperature" not in requests[0]
    assert all(r["reward_valid"] and r["success"] is False for r in results)
    assert results[0]["evaluation"]["judge_call_identity"] == llm.call_identity()
    assert engine.usage()["generated_tokens"] == 300
    assert engine.usage()["requests"] == 2


@pytest.mark.parametrize(
    "kind",
    [
        "truncated",
        "empty",
        "bad_json",
        "bad_structure",
        "bad_message",
        "bad_choices",
        "bad_reasoning",
    ],
)
def test_invalid_thinking_response_never_becomes_zero(
    setup, tmp_path, monkeypatch, kind
):
    engine, task, pending = setup

    def change(response):
        msg = response["choices"][0]["message"]
        if kind == "truncated":
            response["choices"][0]["finish_reason"] = "length"
        elif kind == "empty":
            msg["content"] = None
        elif kind == "bad_json":
            msg["content"] = "not json"
        elif kind == "bad_structure":
            msg["content"] = '{"success": "false"}'
        elif kind == "bad_message":
            response["choices"][0]["message"] = None
        elif kind == "bad_choices":
            response["choices"] = None
        else:
            msg["reasoning_content"] = []

    seen = mock_transport(monkeypatch, change)
    result = finalize(
        {"evaluation": pending},
        lambda _: llm.judge(
            engine,
            task["source_task_id"],
            pending,
            tmp_path / "case",
            purpose="admission",
        ),
    )
    assert result["success"] is None and result["reward_valid"] is False
    assert result["classification"] == "scoring_gap" and len(seen) == 1
    assert (tmp_path / "case/request/response.json").exists()


def test_other_roles_unchanged_and_judge_cache_bound(setup, tmp_path, monkeypatch):
    engine, task, pending = setup
    seen = mock_transport(monkeypatch)
    path = tmp_path / "judge"
    first = llm.judge(
        engine, task["source_task_id"], pending, path, purpose="admission"
    )
    assert (
        llm.judge(engine, task["source_task_id"], pending, path, purpose="admission")
        == first
    )
    assert len(seen) == 1
    cfg = configuration()
    for purpose in (None, "s2_text", "s2_exploration", "s2_solver"):
        config = v2_teacher.configuration(
            task["source_task_id"],
            engine.identity,
            "synthetic",
            10,
            recovery=False,
            runtime=v2_runtime.S2_RUNTIME,
            purpose=purpose,
        )
        assert "judge_text" not in config["teacher"]
        policy = TeacherPolicy(config, {"DEEPSEEK_API_KEY": "synthetic"})
        text = policy.build_text_request([{"role": "user", "content": "x"}], 4096)
        assert text["thinking"] == {"type": "disabled"} and text["temperature"] == 0.2
        assert text["max_tokens"] == 4096 and "reasoning_effort" not in text
        tools = policy.build_request([{"role": "user", "content": "x"}], [], 4096)
        assert (
            tools["thinking"] == {"type": "enabled"} and "reasoning_effort" not in tools
        )
        assert "temperature" not in tools
    assert cfg["student_limits"]["temperature"] == 0.8
    assert cfg["student_limits"]["max_input_tokens"] == 65536
    changed = copy.deepcopy(v2_runtime.s2_execution())
    changed["judge_text"]["reasoning_effort"] = "max"
    monkeypatch.setattr(v2_runtime, "s2_execution", lambda: changed)
    with pytest.raises(ValueError, match="evidence changed"):
        llm.judge(engine, task["source_task_id"], pending, path, purpose="admission")
    assert len(seen) == 1


def test_judge_output_reservation_is_not_old_4096(setup, tmp_path, monkeypatch):
    engine, task, pending = setup
    seen = mock_transport(monkeypatch)
    engine.cfg["s2"]["teacher_budget"]["generated_tokens"] = 4096
    result = finalize(
        {"evaluation": pending},
        lambda _: llm.judge(
            engine,
            task["source_task_id"],
            pending,
            tmp_path / "case",
            purpose="admission",
        ),
    )
    assert not result["reward_valid"] and not seen
    assert "budget exhausted" in result["scoring_fault"]["reason"]


def test_student_probe_reserves_four_current_judge_outputs(setup):
    import runpy

    student_judge_budget = runpy.run_path("scripts/probe_s2_coevolve.py")[
        "student_judge_budget"
    ]
    engine, _, _ = setup
    limits = student_judge_budget(engine.cfg)
    assert limits["requests"] == 4
    assert limits["generated_tokens"] == 4 * llm.call_identity()["max_tokens"] == 32768
    assert limits["seconds"] == 480


def test_student_probe_requires_admission_and_uses_normal_rollout(
    setup, tmp_path, monkeypatch
):
    import runpy

    from recoveragent import s2_backend, v2_s1_batch
    from recoveragent.io import write_json
    from test_s2_experiment import FakeRuntime

    engine, task, _ = setup

    def success(response):
        response["choices"][0]["message"]["content"] = json.dumps(
            {"success": True, "reason": "Synthetic success", "evidence_steps": [0]}
        )

    mock_transport(monkeypatch, success)
    ref = tmp_path / "reference.json"
    write_json(ref, reference(task))
    accepted = llm.admit(engine, task, tmp_path / "admitted", reference_path=ref)
    input_path = tmp_path / "input.json"
    write_json(input_path, {"accepted": [accepted]})
    calls = []

    class Runtime(FakeRuntime):
        def __init__(self, cfg, deployment, output):
            super().__init__(cfg)

        def prepare_rollouts(self, actor):
            pass

        def rollout(self, actor, task, directory, seed, **kwargs):
            assert "diagnostic_goal_probe" not in task
            llm.validate_task(task["generated_task"])
            calls.append(seed)
            return super().rollout(actor, task, directory, seed, **kwargs)

        def update(self, *args, **kwargs):
            pytest.fail("The acceptance probe must never update parameters")

    monkeypatch.setattr(s2_backend, "BackendRuntime", Runtime)
    monkeypatch.setattr(v2_s1_batch, "checkpoint", lambda *args, **kwargs: None)
    student = runpy.run_path("scripts/probe_s2_coevolve.py")["student"]
    output = tmp_path / "student"
    result = student(engine.cfg, {"s0": "S0"}, input_path, output)
    assert result["feedback"]["complete"] and result["training_updates"] == 0
    assert result["success"] == result["failure"] == 2
    student(engine.cfg, {"s0": "S0"}, input_path, output)
    assert len(calls) == 4
    input_path = tmp_path / "unadmitted_input.json"
    write_json(input_path, {"accepted": [task]})
    with pytest.raises(ValueError, match="LLM admission"):
        student(engine.cfg, {"s0": "S0"}, input_path, tmp_path / "unadmitted")


def test_actual_reward_finalize_keeps_null_and_binds_reference_cache(
    setup, tmp_path, monkeypatch
):
    import time

    from recoveragent.s2_backend import BackendRuntime

    engine, task, _ = setup
    accepted = admitted_task(task, tmp_path / "reference_fixture")
    pending = llm.pending(
        accepted,
        reference(task)["messages"],
        {"status": "success", "answer": 0},
        initial_state_hash="clean",
    )

    def unknown(response):
        response["choices"][0]["message"]["content"] = json.dumps(
            {
                "success": None,
                "reason": "Synthetic insufficient evidence",
                "evidence_steps": [0],
            }
        )

    requests = mock_transport(monkeypatch, unknown)
    runtime = BackendRuntime(
        engine.cfg,
        {"deadline_epoch": time.time() + 600, "appworld_root": "synthetic"},
        tmp_path / "runtime",
    )
    raw = {"task_id": task["source_task_id"], "evaluation": pending, "fault": None}
    directory = tmp_path / "episode"
    result = runtime.finalize_reward(raw, directory)
    assert result["success"] is None and not result["reward_valid"]
    assert (
        result["classification"] == "scoring_gap"
        and result["failure_origin"] == "scorer"
    )
    assert result["evaluation"]["judgment"]["success"] is None
    assert runtime.finalize_reward(raw, directory) == result and len(requests) == 1
    content = json.loads(requests[0]["messages"][-1]["content"])
    assert content["reference"] == pending["judge_input"]["reference"]
    changed = copy.deepcopy(raw)
    changed["evaluation"]["reference_identity"]["solver_trajectory_sha256"] = "other"
    with pytest.raises(ValueError, match="raw episode"):
        runtime.finalize_reward(changed, directory)
    assert len(requests) == 1
