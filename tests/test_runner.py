"""Mock rollout tests; real AppWorld acceptance lives in scripts/state_probe.py."""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from recoveragent import runner
from recoveragent.io import digest, write_json
from recoveragent.state import inspect_snapshot

COMPLETE = "supervisor__complete_task"
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": COMPLETE,
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
            },
        },
    }
]
POLICY = {"mock": True}


class Adapter:
    versions = {"mock": True}

    def __init__(self, root, task_id, *args, **kwargs):
        self.task_id, self.value, self.completed = task_id, 0, False
        self.world = SimpleNamespace(
            task=SimpleNamespace(generator_id="fake"),
            task_completed=lambda: self.completed,
        )

    def public_task(self):
        return {"instruction": "fake task"}

    def initial_tools(self):
        return TOOLS

    def execute(self, name, arguments, tools):
        self.value += 1
        self.completed = True
        return "done", tools

    def fingerprint(self):
        return digest(self.value)

    def runtime(self):
        return {"value": self.value}

    def export_state(self, state_id, destination):
        write_json(Path(destination) / "db.json", self.value)

    def save_terminal(self):
        pass

    def close(self):
        pass


@pytest.fixture
def config():
    return {
        "normal": {
            "max_model_turns": 2,
            "max_tool_calls": 2,
            "max_generated_tokens": 10,
        },
        "policy_identity": POLICY,
        "appworld_root": ".",
        "environment_seed": 42,
        "tool_timeout_seconds": 60,
        "inference_url": "http://127.0.0.1:8765",
        "model_timeout_seconds": 1,
        "episode_token_allowance": 10,
        "episode_wall_time_seconds": 10,
        "policy": {"max_new_tokens": 8},
    }


def install_fakes(monkeypatch, text, output):
    class Policy:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, messages, tools, seed, max_new_tokens, config):
            assert all("reward" not in m and "evaluation" not in m for m in messages)
            return {
                "policy": POLICY,
                "text": text,
                "prompt_tokens": 4,
                "generated_tokens": max_new_tokens,
                "token_ids": [1] * max_new_tokens,
            }

    def evaluate(*args, **kwargs):
        assert (output / "terminal" / "manifest.json").exists()
        return SimpleNamespace(success=False, to_dict=lambda: {"success": False})

    monkeypatch.setattr(runner, "AppWorldAdapter", Adapter)
    monkeypatch.setattr(runner, "LocalPolicy", Policy)
    monkeypatch.setitem(
        sys.modules, "appworld.evaluator", SimpleNamespace(evaluate_task=evaluate)
    )


def test_pretermination_excludes_termination_and_evaluator(
    monkeypatch, tmp_path, config
):
    output = tmp_path / "run"
    text = '<tool_call>{"name":"supervisor__complete_task","arguments":{}}</tool_call>'
    install_fakes(monkeypatch, text, output)
    result = runner.run_episode(config, "unit_task", output, 42)
    assert result["status"] == "valid" and result["reward"] == 0
    assert result["termination_reason"] == "completion_claim"
    manifest, context, _ = inspect_snapshot(result["snapshot_ref"])
    assert manifest["position"] == "pre_termination"
    assert all(m["role"] != "tool" for m in context["messages"])
    assert manifest["logical_state_hash"] == digest(0)
    assert inspect_snapshot(output / "terminal")[0]["logical_state_hash"] == digest(1)
    assert result["usage"]["dispatched_tool_calls"] == 1


def test_format_failures_are_bounded_and_never_dispatched(
    monkeypatch, tmp_path, config
):
    output = tmp_path / "run"
    install_fakes(monkeypatch, "invalid call", output)
    result = runner.run_episode(config, "unit_task", output, 42)
    assert result["usage"]["generated_tokens"] == 10
    assert result["usage"]["model_turns"] == 2
    assert result["usage"]["dispatched_tool_calls"] == 0
    assert result["termination_reason"] == "budget_exhausted"
    assert len(list((output / "turns").glob("*.json"))) == 2


def test_infrastructure_has_no_reward(monkeypatch, tmp_path, config):
    class Broken(Adapter):
        def execute(self, *args):
            raise OSError("mock database error")

    output = tmp_path / "run"
    install_fakes(
        monkeypatch,
        '<tool_call>{"name":"supervisor__complete_task","arguments":{}}</tool_call>',
        output,
    )
    monkeypatch.setattr(runner, "AppWorldAdapter", Broken)
    result = runner.run_episode(config, "unit_task", output, 42)
    assert result["status"] == "infrastructure_error"
    assert result["reward"] is None
    assert result["usage"]["generated_tokens"] == 8


def test_uncertain_model_usage_is_reserved_not_fabricated(
    monkeypatch, tmp_path, config
):
    output = tmp_path / "run"
    install_fakes(monkeypatch, "unused", output)

    class BrokenPolicy:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, *args):
            raise TimeoutError("transport interrupted")

    monkeypatch.setattr(runner, "LocalPolicy", BrokenPolicy)
    result = runner.run_episode(config, "unit_task", output, 42)
    assert result["status"] == "infrastructure_error"
    assert result["usage"]["generated_tokens"] == 0
    assert result["usage"]["generation_reservation_tokens"] == 8
