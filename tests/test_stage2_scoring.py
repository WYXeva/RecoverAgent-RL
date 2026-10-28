"""Diagnostic rules only; synthetic tracker fixtures are not AppWorld results."""

import copy
import importlib.util

import pytest

from recoveragent.evaluation import classify_official, exception_observation


@pytest.fixture
def diagnostic():
    spec = importlib.util.spec_from_file_location(
        "stage2", "scripts/probe_stage2_scoring.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_initial_value_error_remains_unknown_and_does_not_invalidate_terminal(
    diagnostic,
):
    try:
        float("synthetic_not_given")
    except ValueError as exc:
        observation = exception_observation(exc, exc.__traceback__, "synthetic_1")
    original = {"success": False, "num_tests": 2, "passes": [{}], "failures": [{}]}
    preserved = copy.deepcopy(original)
    initial = classify_official(original, [observation])
    assert initial["official_success"] is False and initial["success"] is None
    assert initial["category"] == "unknown" and original == preserved
    assert diagnostic.terminal_verdict(initial) == "NOT_VERIFIED"
    terminal = classify_official(
        {"success": True, "num_tests": 2, "passes": [{}, {}], "failures": []}, []
    )
    assert diagnostic.terminal_verdict(terminal) == "PASS"
    assert initial["success"] is None  # Never backfill an earlier unknown.


@pytest.mark.parametrize(
    "category,expected",
    [
        ("task_assertion", "FAIL"),
        ("environment_error", "NOT_VERIFIED"),
        ("unknown_exception", "NOT_VERIFIED"),
    ],
)
def test_terminal_failure_environment_and_unknown_are_distinct(
    diagnostic, category, expected
):
    official = {"success": False, "num_tests": 1, "passes": [], "failures": [{}]}
    result = classify_official(
        official, [{"exception_type": "SyntheticError", "category": category}]
    )
    assert diagnostic.terminal_verdict(result) == expected
    assert result["official"] is official


def test_incomplete_tracker_cannot_pass(diagnostic):
    result = classify_official(
        {"success": True, "num_tests": 2, "passes": [{}], "failures": []}, []
    )
    assert diagnostic.terminal_verdict(result) == "NOT_VERIFIED"


def test_frozen_set_rejects_unlisted_task_before_files_or_world(
    diagnostic, tmp_path, monkeypatch
):
    assert tuple(diagnostic.SOURCES) == ("76f2c72_1", "d0b1f43_1", "07b42fd_1")

    def forbidden(*args):
        pytest.fail("No data access or world initialization outside frozen scope")

    monkeypatch.setattr(diagnostic, "sha256", forbidden)
    monkeypatch.setattr(diagnostic, "AppWorldAdapter", forbidden)
    with pytest.raises(ValueError, match="frozen"):
        diagnostic.probe(tmp_path, "76f2c72_2", "initial", tmp_path / "denied")
    with pytest.raises(ValueError, match="Only initial"):
        diagnostic.probe(tmp_path, "76f2c72_1", "training", tmp_path / "denied")
    assert not (tmp_path / "denied").exists()


def test_changed_source_anchor_rejected_before_state_loading(
    diagnostic, tmp_path, monkeypatch
):
    monkeypatch.setattr(diagnostic, "sha256", lambda _: "changed")
    with pytest.raises(ValueError, match="Trusted dataset manifest changed"):
        diagnostic.probe(tmp_path, "76f2c72_1", "positive", tmp_path / "denied")
    assert not (tmp_path / "denied").exists()
