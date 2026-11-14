"""S1 batch and token-exact inference contracts; synthetic, no paid execution."""

import copy
import math
import time

import pytest

from recoveragent import v2_s1_batch as batch, v2_vllm
from recoveragent.io import read_json, write_json
from recoveragent.v2_runtime import (
    EVALUATION_RUNTIME,
    S1_PREVIOUS_RUNTIME,
    S1_RUNTIME,
    verify_lock,
)
from recoveragent.v2_teacher import configuration, validate_config


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        assert kwargs["enable_thinking"] is False
        return "exact"

    def __call__(self, text, **kwargs):
        return {"input_ids": [1, 2, 3]}

    def decode(self, ids, **kwargs):
        assert kwargs["skip_special_tokens"] is False
        return "<tool_call>synthetic</tool_call><|im_end|>"


def request():
    return dict(
        policy={"checkpoint_id": "S0"},
        messages=[],
        tools=[],
        input_token_ids=[1, 2, 3],
        max_input_tokens=65536,
        max_new_tokens=8,
        top_p=1.0,
        temperature=0.8,
        seed=42,
        eos_token_id=99,
        remaining_wall_seconds=1111.0,
    )


def response():
    return dict(
        id="synthetic",
        model="S0_base",
        choices=[
            dict(
                token_ids=[5, 99],
                logprobs={"token_logprobs": [-0.5, -0.1]},
                finish_reason="stop",
            )
        ],
        usage={"prompt_tokens": 3, "completion_tokens": 2},
    )


def test_batch_frozen_selection_new_lock_and_unchanged_legacy_rejection(
    current_s1_unit_lock,
):
    cfg = batch.configuration()
    assert len(set(cfg["feedback_task_ids"])) == 100
    assert not set(cfg["feedback_task_ids"]) & set(
        cfg["selection"]["excluded_task_ids"]
    )
    assert len(cfg["dev_task_ids"]) == 24
    assert verify_lock(S1_RUNTIME)["revision"] == S1_RUNTIME
    with pytest.raises(ValueError, match="source changed|Incomplete"):
        verify_lock(EVALUATION_RUNTIME)
    with pytest.raises(ValueError, match="source changed|Incomplete"):
        verify_lock(S1_PREVIOUS_RUNTIME)


def test_teacher_uses_new_runtime_provider_and_independent_budget(current_s1_unit_lock):
    cfg = batch.configuration()
    t = configuration(
        cfg["feedback_task_ids"][0],
        cfg["models"]["primary"],
        "synthetic",
        1000,
        runtime=S1_RUNTIME,
    )
    validate_config(t)
    assert t["teacher"]["provider"] == "opencode_go"
    assert t["authorization"]["currency"] == "USD"
    assert t["remaining_requests"] == 80 and t["remaining_generated_tokens"] == 16384
    assert t["limits"]["episode_wall_seconds"] == 1200
    assert t["learning_context"]["max_sequence_tokens"] == 65536
    assert t["environment_contract"] == cfg["environment_contract"]


def test_native_template_token_ids_and_sampling_preserved():
    q = request()
    payload = v2_vllm.request_payload(q, q["policy"], Tokenizer(), "S0_base")
    assert payload["prompt"] == q["input_token_ids"]
    assert payload["max_tokens"] == 8 and payload["temperature"] == 0.8
    assert payload["return_token_ids"] and payload["skip_special_tokens"] is False
    receipt = v2_vllm.response_receipt(
        response(), q, q["policy"], Tokenizer(), "S0_base", 0.2
    )
    assert receipt["token_ids"] == [5, 99] and receipt["finish_reason"] == "eos"
    assert receipt["text"].endswith("<|im_end|>")


@pytest.mark.parametrize("change", ["policy", "tokens", "context", "reserve", "top_p"])
def test_bad_backend_request_is_rejected(change):
    q = request()
    identity = copy.deepcopy(q["policy"])
    if change == "policy":
        q["policy"] = {"checkpoint_id": "raw"}
    elif change == "tokens":
        q["input_token_ids"] = [4]
    elif change == "context":
        q["max_input_tokens"] = 32768
    elif change == "reserve":
        q["max_new_tokens"] = 65536
    else:
        q["top_p"] = 0.9
    with pytest.raises(ValueError):
        v2_vllm.request_payload(q, identity, Tokenizer(), "S0_base")


@pytest.mark.parametrize("change", ["model", "usage", "nan", "length", "token_type"])
def test_invalid_model_token_or_logprob_receipt_is_rejected(change):
    q = request()
    r = response()
    if change == "model":
        r["model"] = "raw_base"
    elif change == "usage":
        r["usage"]["completion_tokens"] = 1
    elif change == "nan":
        r["choices"][0]["logprobs"]["token_logprobs"][0] = math.nan
    elif change == "length":
        r["choices"][0]["token_ids"].append(9)
    else:
        r["choices"][0]["token_ids"][0] = "5"
    with pytest.raises(ValueError):
        v2_vllm.response_receipt(r, q, q["policy"], Tokenizer(), "S0_base", 0.2)


def test_vllm_http_timeout_uses_session_deadline(tmp_path, monkeypatch):
    q = request()
    proof = dict(
        policy=q["policy"],
        model_name="S0_base",
        max_model_len=65536,
        dtype="bfloat16",
        vllm_version="0.19.0",
        logprobs_mode="processed_logprobs",
    )
    write_json(tmp_path / "proof.json", proof)
    backend = v2_vllm.Backend(
        "http://127.0.0.1:18000", tmp_path / "proof.json", q["policy"], "S0_base"
    )
    seen = []

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return response()

    monkeypatch.setattr(
        backend.http, "post", lambda url, **kw: seen.append(kw) or Reply()
    )
    backend(
        None,
        Tokenizer(),
        q,
        identity=q["policy"],
        deadline=time.monotonic() + 2000,
        max_input_tokens=65536,
    )
    assert 1109 < seen[0]["timeout"] <= 1111
    with pytest.raises(TimeoutError):
        backend(
            None,
            Tokenizer(),
            q,
            identity=q["policy"],
            deadline=time.monotonic() - 1,
            max_input_tokens=65536,
        )
    assert len(seen) == 1


def test_resume_config_cannot_change_and_partial_does_not_claim_accuracy(tmp_path):
    batch.initialize(tmp_path, {"frozen": 100}, {"deadline": 1000})
    batch.initialize(tmp_path, {"frozen": 100}, {"deadline": 1000})
    with pytest.raises(ValueError, match="changed"):
        batch.initialize(tmp_path, {"frozen": 99}, {"deadline": 1000})
    s = batch.summarize(
        [{"classification": "success"}, {"classification": "infrastructure_error"}], 100
    )
    assert (
        s["attempted"] == 2
        and s["success_rate_full"] is None
        and s["infrastructure_error"] == 1
    )
    assert read_json(tmp_path / "run_config.json") == {"frozen": 100}


def test_progress_appends_evidence_and_refreshes_latest_without_touching_prior(
    tmp_path,
):
    batch.progress(tmp_path, "feedback", {"attempted": 1})
    batch.progress(tmp_path, "feedback", {"attempted": 2})
    assert read_json(tmp_path / "feedback_summary.json") == {"attempted": 2}
    assert read_json(tmp_path / "progress/feedback/00001.json") == {"attempted": 1}
    assert read_json(tmp_path / "progress/feedback/00002.json") == {"attempted": 2}


def test_prior_infrastructure_attempts_are_carried_without_replay(
    tmp_path, current_s1_unit_lock
):
    cfg = batch.configuration()
    old = read_json("configs/" + cfg["carry_from"]["runtime"] + ".json")
    source = tmp_path / "old"
    output = tmp_path / "new"
    write_json(source / "run_config.json", old)
    policy = {"checkpoint_id": "synthetic", **cfg["models"][cfg["selected_model"]]}
    for task in cfg["carry_from"]["attempted_task_ids"]:
        d = source / "feedback" / task
        write_json(
            d.with_name(task + ".config.json"),
            {
                "model": cfg["models"]["primary"],
                "task_id": task,
                "checkpoint_id": "synthetic",
                "student_limits": cfg["student_limits"],
                "execution_protocol": old["execution_protocol"],
                "purpose": "feedback",
            },
        )
        write_json(
            d / "session/requests/001.request.json",
            {"policy": policy, "input_token_ids": [1]},
        )
        write_json(
            d / "session/requests/001.response.json",
            {
                "policy": policy,
                "input_token_ids": [1],
                "token_ids": [2],
                "generation_seconds": 1.0,
            },
        )
        d.with_name(task + ".worker.log").write_text(
            "NameError: name 'torch' is not defined"
        )
    carried = batch.carry_attempts(
        cfg, {"feedback_source": str(source)}, output, policy
    )
    assert len(carried) == 2 and all(
        r["classification"] == "infrastructure_error" and r["success"] is None
        for r in carried.values()
    )
    assert (
        batch.carry_attempts(cfg, {"feedback_source": str(source)}, output, policy)
        == carried
    )
    first = cfg["carry_from"]["attempted_task_ids"][0]
    (source / "feedback" / first / "session/requests/001.response.json").unlink()
    with pytest.raises(FileNotFoundError):
        batch.carry_attempts(cfg, {"feedback_source": str(source)}, output, policy)


@pytest.mark.parametrize("tamper", [None, "score", "deadline", "source", "policy"])
def test_completed_feedback_handoff_preserves_scores_and_never_replays(
    tmp_path, tamper, current_s1_unit_lock
):
    cfg = batch.configuration()
    previous = read_json("configs/" + cfg["continuation"]["runtime"] + ".json")
    source, output = tmp_path / "source", tmp_path / "next"
    task = cfg["feedback_task_ids"][3]
    policy = {"checkpoint_id": "synthetic"}
    deployment = dict(
        job_started_epoch=1, job_deadline_epoch=100, s0="S0", appworld_root="root"
    )
    write_json(source / "run_config.json", previous)
    write_json(source / "deployment.json", deployment)
    official = {"success": False, "num_tests": 1, "passes": [], "failures": [{}]}
    row = dict(
        task_id=task,
        policy=policy,
        classification="infrastructure_error",
        evaluation={"official": official},
        fault=None,
        experiment_name="synthetic",
        snapshots=[{"snapshot_ref": "read-only-source"}],
    )
    write_json(source / "feedback" / task / "result.json", row)
    summary = {
        "attempted": 1,
        "results": [{**row, "directory": str(source / "feedback" / task)}],
    }
    write_json(source / "feedback_summary.json", summary)
    receipt = {
        "status": "valid",
        "success": False,
        "official": official,
        "task_id": task,
        "experiment": "synthetic",
    }
    if tamper == "score":
        receipt["official"] = {**official, "success": True}
    write_json(tmp_path / "reclassification.json", receipt)
    cfg["continuation"].update(
        summary_sha256=batch.sha256(source / "feedback_summary.json"),
        attempted=1,
        resolved_evaluations={task: batch.sha256(tmp_path / "reclassification.json")},
    )
    cfg["continuation"].pop("prior_carried_sha256", None)
    deployment.update(
        continuation_source=str(source),
        resolved_evaluations={task: str(tmp_path / "reclassification.json")},
    )
    if tamper == "deadline":
        deployment["job_deadline_epoch"] = 200
    elif tamper == "source":
        (source / "feedback" / task / "result.json").write_text("{}")
    elif tamper == "policy":
        policy = {"checkpoint_id": "other"}
    if tamper:
        with pytest.raises(ValueError):
            batch.carry_completed(cfg, deployment, output, policy, {})
        return
    carried = batch.carry_completed(cfg, deployment, output, policy, {})
    assert carried[task]["classification"] == "natural_failure"
    assert carried[task]["original_classification"] == "infrastructure_error"
    assert carried[task]["evaluation"]["official"] == official
    assert read_json(source / "feedback" / task / "result.json") == row
    assert batch.carry_completed(cfg, deployment, output, policy, {}) == carried


@pytest.mark.parametrize(
    "classification,fault,category,stop",
    [
        ("success", None, "task_success", False),
        ("natural_failure", None, "task_failure", False),
        ("infrastructure_error", None, "unknown", False),
        ("infrastructure_error", None, "environment_error", True),
        ("infrastructure_error", {"type": "OutOfMemoryError"}, "unknown", True),
    ],
)
def test_unknown_isolated_score_is_recorded_without_stopping_unrelated_tasks(
    classification, fault, category, stop
):
    assert (
        batch.stops_feedback(
            dict(
                classification=classification,
                fault=fault,
                evaluation={"category": category},
            )
        )
        is stop
    )
