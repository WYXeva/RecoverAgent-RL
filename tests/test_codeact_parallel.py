"""Real CPU child processes and synthetic environments; no GPU/model calls."""

import copy
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_codeact_reboot import response

from recoveragent.codeact import episode, experiment, progress
from recoveragent.io import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


def settings():
    return read_json(ROOT / "configs/codeact_grpo_coevolve_parallel_20261120.json")


class World:
    def __init__(self, effect=None, observation=None):
        self.task = SimpleNamespace(
            instruction="Synthetic count", supervisor={}, datetime="2020-01-01"
        )
        self.shell = SimpleNamespace(user_ns={"counter": 0})
        self.business = 0
        self.models = SimpleNamespace(model_hashes=lambda: {"count": self.business})
        self.effect = effect
        self.observation = (
            observation
            or "Execution failed. Traceback:\nException: Response status code is 422:\nInvalid parameter"
        )
        self.done = False

    def execute(self, code):
        if self.effect == "python":
            self.shell.user_ns["counter"] += 1
        elif self.effect == "database":
            self.business += 1
        return self.observation

    def task_completed(self):
        return self.done


class Client:
    def __init__(self, config, *, student=True, change=False):
        self.usage = {"generated_tokens": 0}
        self.execution = progress.execution_identity(config) if student else None
        self.messages = []
        self.change = change

    def generate(self, messages, directory, index, remaining):
        self.messages.append(copy.deepcopy(messages))
        self.usage["generated_tokens"] += 1
        return response("print(x)" if not self.change else f"print({index})", index)

    def remember(self, *args):
        pass


def test_repeat_error_warns_once_stops_at_five_preserves_history(tmp_path):
    cfg = settings()
    client = Client(cfg)
    result = episode.loop(World(), client, cfg, tmp_path)
    assert result["termination"] == "repeated_error_limit"
    assert result["fault"] is None and len(result["actions"]) == 5
    assert result["usage"]["generated_tokens"] == 5
    assert sum(m.get("content") == progress.NOTICE for m in result["messages"]) == 1
    assert client.messages[3][-1]["content"] == progress.NOTICE
    assert len([m for m in result["messages"] if m["role"] == "assistant"]) == 5
    assert len([m for m in result["messages"] if m["role"] == "tool"]) == 5
    assert len(list((tmp_path / "actions").glob("*.intent.json"))) == 5


@pytest.mark.parametrize(
    "case",
    [
        "python",
        "database",
        "opaque",
        "change",
        "normal",
        "timeout",
        "rate_limit",
        "teacher",
    ],
)
def test_progress_or_uncertainty_never_early_stops(tmp_path, case):
    cfg = settings()
    cfg["limits"]["max_requests"] = 7
    world = World(effect=case)
    if case == "opaque":
        # Added after the runtime baseline is taken, like a user-created generator.
        def execute(code):
            world.shell.user_ns["iterator"] = iter([1, 2])
            return world.observation

        world.execute = execute
    if case in ("normal", "timeout", "rate_limit"):
        world.observation = {
            "normal": "Same successful output",
            "timeout": "Execution failed. Execution timed out",
            "rate_limit": "Execution failed. Response status code is 429",
        }[case]
    result = episode.loop(
        world,
        Client(cfg, student=case != "teacher", change=case == "change"),
        cfg,
        tmp_path,
    )
    assert result["termination"] == "request_limit" and len(result["actions"]) == 7
    assert progress.NOTICE not in [m.get("content") for m in result["messages"]]


def test_environment_fault_does_not_become_repeated_model_failure(tmp_path):
    cfg = settings()
    result = episode.loop(
        World(observation="Execution failed.\nMemoryError: unavailable"),
        Client(cfg),
        cfg,
        tmp_path,
    )
    assert result["termination"] == "environment_error" and result["fault"]
    assert len(result["actions"]) == 1


def synthetic_member(config, deployment, request, directory, member):
    """Spawned process calls the production member lifecycle with network/world mocks."""
    receipt = {"adapter_sha256_after": "synthetic_same_actor"}

    def run(task, attempt, target, root, cfg, client, **kwargs):
        target.mkdir()
        world = World()
        assert world.shell.user_ns["counter"] == 0  # fresh Python state for every child
        time.sleep(0.3)
        trace = episode.loop(world, Client(cfg), cfg, target)
        trace.update(initial_state_hash="same_clean_initial_state")
        write_json(target / "trajectory.json", trace)
        write_json(
            target / "result.json",
            {
                "termination": trace["termination"],
                "usage": trace["usage"],
                "seconds": 0.3,
                "success": member == 0,
                "valid": True,
            },
        )
        write_json(
            target / "isolation.json", {"pid": os.getpid(), "initial_counter": 0}
        )

    with (
        patch.object(experiment, "verify_checkpoint", return_value=receipt),
        patch.object(experiment, "VLLMStudent", return_value=None),
        patch.object(experiment, "run", side_effect=run),
    ):
        return experiment.sample_member(config, deployment, request, directory, member)


def test_two_parallel_environments_refill_resume_and_policy_binding(
    tmp_path, monkeypatch
):
    cfg = settings()
    req = {
        "index": 3,
        "actor": "same_actor",
        "selection": {"task_id": cfg["online"]["train_tasks"][0]},
        "server": {"url": "mock", "max_num_seqs": 2},
        "server_receipt": "mock",
    }
    dep = {"deadline": time.time() + 60, "appworld_root": "mock"}
    original = experiment.sample_member
    monkeypatch.setattr(experiment, "sample_member", synthetic_member)
    result = experiment.sample_group(cfg, dep, req, tmp_path / "group")
    rows = result["episodes"]
    assert len({r["worker_pid"] for r in rows}) == 4
    assert max(rows[0]["started"], rows[1]["started"]) < min(
        rows[0]["finished"], rows[1]["finished"]
    )
    events = sorted(
        [(r["started"], 1) for r in rows] + [(r["finished"], -1) for r in rows]
    )
    active = peak = 0
    for _, delta in events:
        active += delta
        peak = max(active, peak)
    assert peak == 2
    assert len({r["seed"] for r in rows}) == 4
    assert all(
        r["policy"] == rows[0]["policy"]
        and r["initial_state_hash"] == "same_clean_initial_state"
        for r in rows
    )
    assert result["effective"]
    # Resume reuses complete evidence, without executing/rewarding a second time.
    monkeypatch.setattr(experiment, "sample_member", original)
    cached = synthetic_member(cfg, dep, req, tmp_path / "group", 0)
    assert cached == rows[0]
    with pytest.raises(ValueError, match="Cached rollout"):
        synthetic_member(
            {k: v for k, v in cfg.items() if k != "student_execution"},
            dep,
            req,
            tmp_path / "group",
            0,
        )


def test_concurrency_settings_do_not_silently_change_server_capacity(tmp_path):
    cfg = settings()
    assert experiment.sampling_concurrency(cfg) == 2
    cfg["online"]["sampling_concurrency"] = 4
    assert experiment.sampling_concurrency(cfg) == 4
    with pytest.raises(ValueError, match="vLLM server concurrency"):
        experiment.sample_group(cfg, {}, {"server": {"max_num_seqs": 2}}, tmp_path)
    cfg["online"]["sampling_concurrency"] = True
    with pytest.raises(ValueError, match="integer"):
        experiment.sampling_concurrency(cfg)
