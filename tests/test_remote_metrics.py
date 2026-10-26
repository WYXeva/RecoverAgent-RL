"""Software fixtures for receipt accounting and unknown evaluation denominators."""

from pathlib import Path

import pytest

from recoveragent.io import write_json


def test_receipt_accounting_rejects_incorrect_known_token_count(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    from summarize_remote_validation import session_metrics

    write_json(
        tmp_path / "requests/001.request.json",
        {"input_token_ids": [1, 2], "max_new_tokens": 4096},
    )
    write_json(tmp_path / "requests/001.response.json", {"token_ids": [3]})
    usage = {"model_requests": 1, "prompt_tokens": 2, "generated_tokens": 1}
    assert session_metrics(tmp_path, usage)["max_input_tokens"] == 2
    with pytest.raises(AssertionError):
        session_metrics(tmp_path, {**usage, "generated_tokens": 99})


def test_unknown_evaluations_stay_separate_from_official_failures(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    from summarize_remote_validation import aggregate

    result = aggregate(
        [
            {"status": "valid", "official_success": True},
            {"status": "valid", "official_success": False},
            {"status": "evaluation_error", "official_success": None},
        ]
    )
    assert result["attempts"] == 3
    assert result["valid_official_evaluations"] == 2
    assert result["unknown_outcomes"] == 1
    assert result["success_rate_over_valid"] == 0.5
    assert result["confirmed_success_fraction_over_attempts"] == 1 / 3
