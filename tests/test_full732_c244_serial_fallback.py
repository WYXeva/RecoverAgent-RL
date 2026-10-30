"""Serial fallback preserves failed attempts and cannot relax acceptance gates."""

import copy
import inspect

import pytest

from recoveragent import full732_c244_serial_fallback as s
from recoveragent import student_evaluation
from test_full732_c244 import config


def prior():
    cfg = config()
    rows = [
        dict(
            cfg["cases"][i],
            status=status,
            official_success=success,
            policy=cfg["expected_policy"],
        )
        for i, (status, success) in enumerate(
            [
                ("valid", True),
                ("evaluation_error", None),
                ("infrastructure_error", None),
            ]
        )
    ]
    rows[2]["error_type"] = "TimeoutError"
    plan = {
        "inherited_count": 3,
        "allowed_prior_transport_errors": {rows[2]["task_id"]: "TimeoutError"},
    }
    return cfg, {"rows": rows}, plan


def test_unknown_attempts_are_preserved_and_not_scheduled_again():
    cfg, previous, plan = prior()
    unchanged = copy.deepcopy(previous)
    rows = s.validate_prior(cfg, previous, plan)
    result = s.summary(cfg, rows)
    assert previous == unchanged
    assert (
        result["attempted"],
        result["success"],
        result["failure"],
        result["unknown"],
    ) == (3, 1, 0, 2)
    assert result["unattempted"] == cfg["cases"][3:]
    assert result["TGC"] is None and result["max_concurrent_tasks"] == 1


@pytest.mark.parametrize("error", ["OutOfMemoryError", "ValueError", "RuntimeError"])
def test_no_fallback_from_unapproved_failure(error):
    cfg, previous, plan = prior()
    previous["rows"][2]["error_type"] = error
    with pytest.raises(ValueError, match="recorded transport"):
        s.validate_prior(cfg, previous, plan)


def test_cannot_drop_or_reorder_prior_attempts():
    cfg, previous, plan = prior()
    previous["rows"].reverse()
    with pytest.raises(ValueError, match="skip or retry"):
        s.validate_prior(cfg, previous, plan)


def test_cannot_inherit_another_policy():
    cfg, previous, plan = prior()
    previous["rows"][0]["policy"] = {"checkpoint_id": "another"}
    with pytest.raises(ValueError, match="policy changed"):
        s.validate_prior(cfg, previous, plan)


def test_original_request_timeout_is_not_extended():
    assert s.request_json is student_evaluation.request_json
    assert inspect.signature(s.request_json).parameters["timeout"].default == 180
