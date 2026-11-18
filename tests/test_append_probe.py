"""CPU orchestration mocks only; no GPU, AppWorld task or Teacher request."""

from types import SimpleNamespace
import time

import pytest

from recoveragent.io import read_json, write_json
from scripts import probe_s2_append as probe


def test_probe_one_group_all_samples_feedback_no_updates(tmp_path, monkeypatch):
    from recoveragent import s2_backend
    from recoveragent.v2_grpo import group_advantages

    cfg = read_json("configs/v2_s2_static_evolve_20261116.json")
    spec = read_json("configs/s2_append_probe_20261118.json")
    plan = {**spec, "actor": "frozen15", "config": cfg}
    monkeypatch.setattr(probe, "frozen", lambda _: plan)
    seen, stopped = [], []

    class Runtime:
        def __init__(self, config, deployment, output):
            assert config == cfg

        def prepare_rollouts(self, actor):
            assert actor == "frozen15"

        def stop(self):
            stopped.append(True)

        def rollout(self, actor, task, directory, seed):
            assert task == {"task_id": spec["task_id"]}  # Original; no Judge.
            seen.append(seed)
            write_json(directory / "history.json", {"messages": []})
            return {
                "task_id": task["task_id"],
                "generated_task_id": None,
                "initial_state_hash": "same",
                "policy": {"checkpoint_id": actor},
                "success": seed == 42,
                "reward_valid": True,
                "classification": "success" if seed == 42 else "natural_failure",
                "directory": str(directory),
            }

    monkeypatch.setattr(s2_backend, "BackendRuntime", Runtime)
    deploy = tmp_path / "deployment.json"
    write_json(deploy, {})
    probe.sample(
        SimpleNamespace(
            output=tmp_path, deployment=deploy, deadline_epoch=time.time() + 3600
        )
    )
    result = read_json(tmp_path / "sampling/group.json")
    assert seen == [42, 142, 242, 342] and stopped == [True]
    assert result["optimizer_updates"] == 0
    assert result["feedback"]["outcomes"] == [True, False, False, False]
    assert "boundary" in result["feedback"]["signals"]
    assert result["advantages"] == group_advantages(result["episodes"])
    with pytest.raises(FileExistsError):
        probe.sample(
            SimpleNamespace(
                output=tmp_path, deployment=deploy, deadline_epoch=time.time() + 3600
            )
        )


def test_new_encoding_in_state_identity_legacy_unchanged(monkeypatch):
    from recoveragent.s2_state import protocol_identity

    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", "v2_s2_append_20261118")
    assert protocol_identity()["student_input_encoding"] == "student_append_tokens_v1"
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", "v2_s2_efficiency_20261118")
    assert "student_input_encoding" not in protocol_identity()
