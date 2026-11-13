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
    "statement,task_file,expected",
    [
        ("predicted_answer = float(predicted_answer)", True, "task_answer_failure"),
        ("ground_truth_answer = float(ground_truth_answer)", True, "unknown_exception"),
        ("predicted_answer = int(predicted_answer)", True, "unknown_exception"),
        ("float(predicted_answer)", True, "unknown_exception"),
        ("predicted_answer = float(predicted_answer)", False, "unknown_exception"),
    ],
)
def test_only_direct_official_predicted_number_conversion_is_task_failure(
    tmp_path, statement, task_file, expected
):
    path = tmp_path / (
        "tasks/sample_1/ground_truth/evaluation.py" if task_file else "library.py"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    source = (
        "predicted_answer = ground_truth_answer = 'synthetic-invalid'\n" + statement
    )
    path.write_text(source)
    obs = observation(source, str(path))
    assert obs["category"] == expected
    result = classify_official(
        {"success": False, "num_tests": 1, "passes": [], "failures": [{}]}, [obs]
    )
    assert (result["success"] is False) == (expected == "task_answer_failure")
    assert "synthetic-invalid" not in json.dumps(obs)


@pytest.mark.parametrize(
    "left,right,task_call,expected",
    [
        ("12", "invalid", True, "task_answer_failure"),
        ("12", None, True, "task_answer_failure"),
        ("invalid", "12", True, "unknown_exception"),
        ("invalid", "invalid", True, "unknown_exception"),
        ("12", "invalid", False, "unknown_exception"),
    ],
)
def test_official_float_normalizer_distinguishes_answer_from_reference_or_library(
    tmp_path, left, right, task_call, expected
):
    common = tmp_path / "appworld/common/evaluation.py"
    common.parent.mkdir(parents=True)
    code = (
        "def assert_plus(left, condition, right, normalize):\n"
        "    left = normalize(left)\n"
        "    right = normalize(right)\n"
    )
    common.write_text(code)
    namespace = {}
    exec(compile(code, str(common), "exec"), namespace)
    path = tmp_path / (
        "tasks/sample_1/ground_truth/evaluation.py" if task_call else "loader.py"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    code = "test.case(ground_truth_answer, '==', predicted_answer, normalize=float)"
    path.write_text(code)
    values = dict(
        test=SimpleNamespace(case=namespace["assert_plus"]),
        ground_truth_answer=left,
        predicted_answer=right,
    )
    try:
        exec(compile(code, str(path), "exec"), values)
    except Exception as exc:
        obs = exception_observation(exc, exc.__traceback__, "sample_1")
    assert obs["category"] == expected
    assert "invalid" not in json.dumps(obs)


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
