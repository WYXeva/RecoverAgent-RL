"""Software evidence; no hidden tasks or performance claims."""

import json
import sys
from types import SimpleNamespace

import pytest

from recoveragent.evaluation import (
    classify_official,
    exception_observation,
    evaluate_here,
)
from recoveragent.harness import (
    SCHEMA_REVISION,
    RUNTIME_REVISION,
    ProtocolHarness,
    ProtocolViolation,
    check_batch,
)
from test_harness import session
from test_student_batch import block, receipt


def observation(source, filename):
    try:
        exec(compile(source, filename, "exec"))
    except Exception as exc:
        return exception_observation(exc, exc.__traceback__, "sample_1")
    raise AssertionError("fixture must throw")


@pytest.mark.parametrize(
    "source", ["[][0]", '{}["missing"]', "def f():\n if False: a=1\n return a\nf()"]
)
def test_task_state_exception_is_official_failure_only_with_complete_tracker(source):
    obs = observation(source, "/data/tasks/sample_1/ground_truth/evaluation.py")
    official = {"success": False, "num_tests": 2, "passes": [{}], "failures": [{}]}
    result = classify_official(official, [obs])
    assert result["category"] == "task_failure" and result["success"] is False
    assert result["official"] is official
    assert classify_official({**official, "num_tests": 3}, [obs])["success"] is None
    assert classify_official(official, [])["success"] is None


@pytest.mark.parametrize(
    "source,filename,category",
    [
        ("[][0]", "/library/loader.py", "unknown_exception"),
        (
            'raise RuntimeError("opaque")',
            "/data/tasks/sample_1/ground_truth/evaluation.py",
            "unknown_exception",
        ),
        (
            'raise OSError("db unavailable")',
            "/data/tasks/sample_1/ground_truth/evaluation.py",
            "environment_error",
        ),
        (
            'raise AssertionError("hidden")',
            "/data/tasks/sample_1/ground_truth/evaluation.py",
            "task_assertion",
        ),
    ],
)
def test_origin_and_environment_are_not_inferred_from_type_alone(
    source, filename, category
):
    obs = observation(source, filename)
    assert obs["category"] == category
    result = classify_official(
        {"success": False, "num_tests": 1, "passes": [], "failures": [{}]}, [obs]
    )
    assert (result["success"] is False) == (category == "task_assertion")
    assert "hidden" not in json.dumps(obs)


def test_observer_preserves_original_tracker_and_profile(monkeypatch):
    class Tracker:
        def __init__(self):
            self.failures = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type:
                self.failures.append({"opaque_original": True})
            return True

        def to_dict(self):
            return {
                "success": False,
                "num_tests": 1,
                "passes": [],
                "failures": self.failures,
            }

    def evaluate(*a, **k):
        tracker = Tracker()
        with tracker:
            exec(
                compile(
                    "[][0]", "/data/tasks/sample_1/ground_truth/evaluation.py", "exec"
                )
            )
        return tracker

    original = Tracker.__exit__
    profile = sys.getprofile()
    monkeypatch.setitem(
        sys.modules,
        "appworld.evaluator",
        SimpleNamespace(TestTracker=Tracker, evaluate_task=evaluate),
    )
    result = evaluate_here("sample_1", "fixture")
    assert result["category"] == "task_failure"
    assert result["official"]["failures"] == [{"opaque_original": True}]
    assert Tracker.__exit__ is original and sys.getprofile() is profile


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({}, {"rule": "required", "missing_fields": ["app_name"]}),
        (
            {"app_name": "demo", "access_token": "SECRET"},
            {"rule": "additionalProperties", "unknown_fields": ["access_token"]},
        ),
        (
            {"app_name": 7},
            {"rule": "type", "path": ["app_name"], "expected_type": "string"},
        ),
    ],
)
def test_schema_feedback_and_teacher_student_parity(monkeypatch, arguments, expected):
    from recoveragent.student import parse_batch
    from test_student_batch import DISCOVER

    monkeypatch.setitem(
        DISCOVER["function"],
        "parameters",
        {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
            "required": ["app_name"],
        },
    )
    s = session(monkeypatch, harness_revision=SCHEMA_REVISION)
    # Standalone shared Teacher check and Student check must report identically.
    text = block("api_docs__show_api_doc", arguments)
    try:
        check_batch(parse_batch(text, "teacher", "stop"), s.messages, s.tools)
    except ProtocolViolation as exc:
        shared = ProtocolHarness(
            "dynamic_discovery", revision=SCHEMA_REVISION
        ).rejection_event(exc, truncated=False, guard=None)
    event = s.step(receipt(text))
    feedback = json.loads(event["feedback"]["content"])
    assert shared["feedback"] == event["feedback"]
    assert all(feedback["schema_error"][k] == v for k, v in expected.items())
    assert "SECRET" not in json.dumps(feedback) and not s.adapter.calls
    for _ in range(2):
        s.step(receipt(text))
    assert s.termination == "protocol_correction_exhausted"


def test_legacy_schema_feedback_is_unchanged():
    h = ProtocolHarness("dynamic_discovery", revision=RUNTIME_REVISION)
    feedback = h.reject("invalid_arguments", schema_error={"missing_fields": ["x"]})
    assert "schema_error" not in json.loads(feedback["content"])
