"""Synthetic error classification; live CPU API probes are reported separately."""

import hashlib
import json

import pytest

from recoveragent import appworld_environment as environment
from recoveragent import student, v2_episode
from recoveragent.io import read_json
from recoveragent.v2_runtime import EVALUATION_RUNTIME
from recoveragent.v2_tasks import plan
import test_nullable_recovery as fixtures
from test_student_batch import Tokenizer, block, receipt

worlds = fixtures.worlds


DATABASE_ERROR = (
    "Execution failed. Traceback:\n"
    "sqlalchemy.exc.StatementError: (builtins.ValueError) "
    "Datetime values must have timezone information."
)


@pytest.mark.parametrize(
    "result,expected",
    [
        (DATABASE_ERROR, True),
        ("Execution failed. Traceback:\nsqlite3.OperationalError: disk full", True),
        ("Execution failed. Traceback:\nMemoryError: failed", True),
        ("Execution failed. Traceback:\nException: Response status code is 500", True),
        (
            "Execution failed. Traceback:\nException: Response status code is 422:\nexpired card",
            False,
        ),
        ("Execution failed. Traceback:\nException: Response status code is 401", False),
        (json.dumps({"body": DATABASE_ERROR}), False),
        ("ordinary text about sqlalchemy.exc.StatementError", False),
    ],
)
def test_error_envelope_distinguishes_infrastructure_from_business(result, expected):
    assert bool(environment.execution_environment_error(result)) is expected


def test_incompatible_dependency_version_rejected_before_world(monkeypatch):
    monkeypatch.setattr(
        environment.importlib.metadata, "version", lambda name: "0.0.48"
    )
    with pytest.raises(ValueError, match="dependency versions differ"):
        environment.verify_environment(environment.REVISION)
    with pytest.raises(ValueError, match="Unknown"):
        environment.verify_environment("unknown")


@pytest.mark.parametrize("infrastructure", [True, False])
def test_worker_stops_before_later_batch_calls_and_preserves_real_error(
    worlds, tmp_path, monkeypatch, infrastructure
):
    cfg = read_json("configs/v2_base_dev24_envfix_20261113.json")
    contract = read_json("configs/v2_base_dev24_envfix_20261113_lock.json")[
        "session_contract"
    ]
    model = {
        "model": "synthetic",
        "revision": "synthetic",
        "thinking": False,
        "chat_template_sha256": hashlib.sha256(
            Tokenizer.chat_template.encode()
        ).hexdigest(),
        "tool_format": "qwen_json",
    }
    monkeypatch.setattr("recoveragent.v2_model.validate_identity", lambda x: x)
    monkeypatch.setattr(
        student, "verify_student_runtime", lambda _: {"session_contract": contract}
    )
    monkeypatch.setattr(v2_episode, "load_tokenizer", lambda _: Tokenizer())
    monkeypatch.setattr(v2_episode, "environment_check", lambda: None)
    monkeypatch.setattr(environment, "verify_environment", lambda r: {"revision": r})
    error = (
        DATABASE_ERROR
        if infrastructure
        else "Execution failed. Traceback:\nException: Response status code is 422:\nnormal business error"
    )
    calls = []

    def adapter(root, task, *args, **kwargs):
        value = worlds(task, kwargs["research"])
        execute = value.execute

        def dispatch(name, arguments, tools):
            calls.append(name)
            if name == "gmail__show_category_sizes":
                return error, tools
            return execute(name, arguments, tools)

        value.execute = dispatch
        return value

    monkeypatch.setattr(v2_episode, "AppWorldAdapter", adapter)
    evaluations = []

    def evaluate(*args, **kwargs):
        evaluations.append(True)
        return {"status": "valid", "success": False}

    monkeypatch.setattr(v2_episode, "evaluate_isolated", evaluate)
    responses = iter(
        [
            block(
                "api_docs__show_api_doc",
                {"app_name": "gmail", "api_name": "show_category_sizes"},
            ),
            block(
                "gmail__show_category_sizes",
                {"access_token": "synthetic", "read": None},
            )
            + block("supervisor__complete_task", {}),
        ]
    )
    directory = tmp_path / "episode"
    result = v2_episode.run(
        {
            "task_id": plan()["dev24"][0],
            "output": str(directory),
            "appworld_root": "synthetic",
            "purpose": "evaluation",
            "model": model,
            "checkpoint_id": "S0_synthetic",
            "student_limits": cfg["student_limits"],
            "environment_contract": environment.REVISION,
            "execution_protocol": EVALUATION_RUNTIME,
        },
        lambda request: receipt(next(responses))(request),
    )
    assert (directory / "history.json").exists()
    if infrastructure:
        assert result["classification"] == "infrastructure_error"
        assert result["success"] is None and not evaluations
        assert "supervisor__complete_task" not in calls
        saved = read_json(directory / "execution_environment_error.json")
        assert saved["result"] == error and saved["automatic_retry"] is False
        assert result["fault"]["type"] == "ExecutionEnvironmentError"
        assert (directory / "session/interruption/context.json").exists()
    else:
        assert result["classification"] == "natural_failure" and evaluations
        assert "supervisor__complete_task" in calls
        assert result["fault"] is None
