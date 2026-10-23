"""Software guards only; actual GPU generation is recorded separately."""

import copy
import json
from pathlib import Path

import pytest

from recoveragent.student_backend import validate_request
from recoveragent.student_evaluation import evaluation_prompt
from recoveragent.answer_guard import RULES as ANSWER_RULES
from recoveragent.harness import RULES as HARNESS_RULES


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        assert kwargs["enable_thinking"] is False
        return json.dumps([messages, kwargs["tools"]])

    def __call__(self, text, **kwargs):
        return {"input_ids": list(text.encode())}


def request():
    t = Tokenizer()
    r = {
        "policy": {"checkpoint_id": "test"},
        "messages": [{"role": "user", "content": "test"}],
        "tools": [],
        "max_new_tokens": 4096,
    }
    r["input_token_ids"] = t(
        t.apply_chat_template(r["messages"], tools=[], enable_thinking=False)
    )["input_ids"]
    return r


def test_backend_binds_actual_checkpoint_input_and_full_window():
    r = request()
    assert validate_request(r, r["policy"], Tokenizer()) == r["input_token_ids"]
    for field, value in [
        ("policy", {"checkpoint_id": "wrong"}),
        ("input_token_ids", [0]),
        ("max_new_tokens", 4097),
        ("max_new_tokens", True),
    ]:
        bad = {**copy.deepcopy(r), field: value}
        with pytest.raises(ValueError):
            validate_request(bad, r["policy"], Tokenizer())
    long = copy.deepcopy(r)
    long["messages"][0]["content"] = "x" * 30000
    long["input_token_ids"] = Tokenizer()(
        Tokenizer().apply_chat_template(
            long["messages"], tools=[], enable_thinking=False
        )
    )["input_ids"]
    with pytest.raises(ValueError, match="budget"):
        validate_request(long, r["policy"], Tokenizer())


def test_frozen_eval_plan_and_shared_prompt():
    config = json.loads(Path("configs/p3b_paired_evaluation_20261023.json").read_text())
    prompt = evaluation_prompt(config)
    assert ANSWER_RULES in prompt and HARNESS_RULES in prompt
    assert "4096" in prompt and "16384" in prompt
    assert len({c["task_id"] for c in config["cases"]}) == 6
    assert sum(c["split"] == "dev" for c in config["cases"]) == 2
    assert config["tool_mode"] == "dynamic_discovery"


def test_evaluation_worker_timeout_keeps_unknown_outcome_and_unattempted(
    tmp_path, monkeypatch
):
    import sys
    import subprocess
    from recoveragent import student_evaluation as evaluation

    config = Path("configs/p3b_paired_evaluation_20261023.json").resolve()
    output = tmp_path / "evaluation"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluation",
            "collect",
            "--config",
            str(config),
            "--output",
            str(output),
            "--arm",
            "raw_base",
        ],
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic worker", 780)

    monkeypatch.setattr(evaluation.subprocess, "run", timeout)
    evaluation.main()
    result = json.loads((output / "summary.json").read_text())
    assert result["attempts"] == 1 and len(result["unattempted"]) == 5
    assert result["rows"][0]["official_success"] is None
    assert result["rows"][0]["usage_unknown"]


def test_cleanup_error_does_not_hide_primary_worker_failure(tmp_path, monkeypatch):
    from recoveragent import student_evaluation as evaluation
    import freezegun

    class BrokenAdapter:
        def fingerprint(self):
            raise ValueError("synthetic primary failure")

        def close(self):
            raise RuntimeError("synthetic cleanup failure")

    monkeypatch.setattr(freezegun, "configure", lambda **kwargs: None)
    monkeypatch.setattr(
        evaluation, "verify_execution_runtime", lambda: {"software_fixture": True}
    )
    monkeypatch.setattr(evaluation, "environment_check", lambda: {})
    monkeypatch.setattr(evaluation, "load_tokenizer", lambda: object())
    monkeypatch.setattr(evaluation, "request_json", lambda _: {})
    monkeypatch.setattr(evaluation, "EvaluationAdapter", lambda *args: BrokenAdapter())
    config = json.loads(Path("configs/p3b_paired_evaluation_20261023.json").read_text())
    result = evaluation.run_episode(
        config, config["cases"][0], "raw_base", tmp_path / "episode"
    )
    assert result["error_type"] == "ValueError"
    assert result["cleanup_error_type"] == "RuntimeError"
    assert result["official_success"] is None
    assert (tmp_path / "episode/result.json").exists()
