"""Public format guidance only; no answer repair, hidden scorer or task lookup."""

import copy
import json

import pytest

from recoveragent import teacher_followup as followup
from recoveragent.io import write_json, read_json
from recoveragent.teacher_answer_format import (
    NOTICE,
    PROTOCOL,
    public_task,
    validate_record,
)


def test_notice_keeps_task_system_and_unclassified_answer_semantics():
    source = {
        "instruction": "Return the exact identifier 001-02.",
        "datetime": "synthetic",
    }
    original = copy.deepcopy(source)
    assert public_task(source, {}) == original
    result = public_task(source, {"answer_format_contract": PROTOCOL})
    assert source == original and result["instruction"] == original["instruction"]
    assert result["answer_format_contract"] == NOTICE
    assert "identifiers" in NOTICE["rule"] and "explicit formatting" in NOTICE["rule"]
    record = {
        "teacher": {"answer_format_contract": PROTOCOL},
        "messages": [{}, {"content": json.dumps(result)}],
    }
    validate_record(record)
    record["messages"][1]["content"] = json.dumps(source)
    with pytest.raises(ValueError, match="notice"):
        validate_record(record)
    with pytest.raises(ValueError, match="Unknown"):
        public_task(source, {"answer_format_contract": "unknown"})


def test_carried_attempt_cannot_reexecute_or_change_its_result(tmp_path, monkeypatch):
    from recoveragent.demonstrations import sha256

    previous = tmp_path / "old/result.json"
    entry = {"task_id": "synthetic", "teacher": "deepseek", "eligible": False}
    write_json(previous, entry)
    campaign = tmp_path / "new"
    write_json(
        campaign / "campaign.json",
        {
            "carried_attempts": {
                "synthetic:deepseek": {
                    "path": str(previous),
                    "sha256": sha256(previous),
                }
            }
        },
    )
    monkeypatch.setattr(
        followup.subprocess, "Popen", lambda *a, **kw: pytest.fail("Paid replay")
    )
    result = followup.attempt(campaign, "synthetic", "deepseek", {})
    assert {k: result[k] for k in entry} == entry
    assert read_json(previous) == entry
    previous.write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        followup.attempt(campaign, "synthetic", "deepseek", {})
