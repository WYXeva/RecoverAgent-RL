"""Controller exception classification, no benchmark answers or model calls."""

import types

import pytest

from recoveragent.evaluation import exception_observation


def execute(tmp_path, order, prediction, reference):
    helper = tmp_path / "appworld/common/evaluation.py"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        "def assert_plus(left, op, right, normalize=float):\n    left = normalize(left)\n    right = normalize(right)\n    assert left == right\n"
    )
    namespace = {}
    exec(compile(helper.read_text(), str(helper), "exec"), namespace)
    test = types.SimpleNamespace(case=namespace["assert_plus"])
    task = tmp_path / "tasks/synthetic/ground_truth/evaluation.py"
    task.parent.mkdir(parents=True)
    args = (
        'predicted_answer, "==", ground_truth_answer'
        if order == "left"
        else 'ground_truth_answer, "==", predicted_answer'
    )
    task.write_text(
        "def evaluate(test, predicted_answer, ground_truth_answer):\n    test.case("
        + args
        + ", normalize=float)\n"
    )
    caller = {}
    exec(compile(task.read_text(), str(task), "exec"), caller)
    try:
        caller["evaluate"](test, prediction, reference)
    except Exception as exc:
        return exception_observation(exc, exc.__traceback__, "synthetic")
    raise AssertionError("Expected a failed synthetic check")


@pytest.mark.parametrize("order", ["left", "right"])
@pytest.mark.parametrize("prediction", ["not a number", None])
def test_bad_prediction_is_model_failure_in_either_order(tmp_path, order, prediction):
    result = execute(tmp_path, order, prediction, 12.5)
    assert result["category"] == "task_answer_failure"
    assert set(result) == {"exception_type", "category"}


@pytest.mark.parametrize("order", ["left", "right"])
@pytest.mark.parametrize("reference", ["bad reference", None, float("nan")])
def test_bad_reference_not_mislabeled_as_model_failure(tmp_path, order, reference):
    result = execute(tmp_path, order, "not a number", reference)
    assert result["category"] != "task_answer_failure"
