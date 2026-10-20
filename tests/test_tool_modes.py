"""Synthetic integration tests; real upstream/model acceptance is logged separately."""

import copy
from types import SimpleNamespace

import pytest

from recoveragent import APPWORLD_REVISION, p3a, tool_modes
from recoveragent.compare_tools import summarize_comparison
from recoveragent.demonstrations import (
    export_dataset,
    validate_receipts,
    verify_dataset,
)
from recoveragent.inspect_teacher import build_view
from recoveragent.io import digest, read_json, write_json
from recoveragent.teacher import TeacherPolicy
from test_runner import Adapter, TOOLS
from test_teacher_data import config as teacher_config, response


@pytest.fixture
def config():
    return teacher_config.__wrapped__()


def predictor_fixture(monkeypatch, config, truncated=False):
    class World(Adapter):
        versions = {"revision": APPWORLD_REVISION, "data_version": "SYNTHETIC"}
        catalogue = TOOLS

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.world.task.api_docs = {"supervisor": {"complete_task": {}}}

    class Predictor:
        def build_messages(self, task, include_cache_control):
            assert include_cache_control is False
            # This test task has no ground_truth property at all.
            return [{"role": "user", "content": "public synthetic task and catalogue"}]

        def predicted_output_to_apis(self, task, text):
            assert text == "supervisor.complete_task"
            return ["supervisor__complete_task"]

    requests = []

    def transport(payload):
        requests.append(copy.deepcopy(payload))
        result = response()
        if "tools" not in payload:
            result["choices"] = [
                {
                    "finish_reason": "length" if truncated else "stop",
                    "message": {
                        "role": "assistant",
                        "content": "supervisor.complete_task",
                    },
                }
            ]
        return result

    monkeypatch.setattr(tool_modes, "official_predictor", lambda *args: Predictor())
    monkeypatch.setattr(p3a, "AppWorldAdapter", World)
    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda cfg, creds: TeacherPolicy(cfg, creds, transport=transport),
    )
    monkeypatch.setattr(
        p3a, "official_evaluate", lambda *args: (not truncated, {"synthetic": True})
    )
    config["tool_mode"] = "api_predictor"
    return requests


def test_selector_receipts_cost_and_dataset_separation(tmp_path, monkeypatch, config):
    requests = predictor_fixture(monkeypatch, config)
    run = tmp_path / "run"
    write_json(run / "config.json", config)
    output = run / "teacher_synthetic"
    trajectory = p3a.run_teacher_episode(
        config, "fake", output, {"DEEPSEEK_API_KEY": "synthetic"}
    )
    assert trajectory["status"] == "valid"
    assert trajectory["usage"]["provider_requests"] == 2
    assert trajectory["usage"]["model_turns"] == 1
    assert trajectory["usage"]["generated_tokens"] == 40
    assert requests[0]["max_tokens"] == requests[1]["max_tokens"] == 4096
    assert "tools" not in requests[0]
    assert requests[1]["tools"] == TOOLS
    assert "selected before execution" in requests[1]["messages"][0]["content"]
    assert trajectory["initial_tools"] == TOOLS
    validate_receipts(trajectory, output)
    dataset = tmp_path / "dataset"
    manifest = export_dataset(run, dataset, {"fake"}, config["limits"])
    assert manifest["counts"]["accepted"] == 1
    verify_dataset(dataset, {"fake"})
    sample = read_json(dataset / "manifest.json")
    assert sample["costs"]["provider_requests"] == 2
    import json

    demonstration = json.loads((dataset / "demonstrations.jsonl").read_text())
    assert demonstration["tool_mode"] == "api_predictor"
    assert len(demonstration["turns"]) == 1
    assert "predictor" not in demonstration
    view = build_view(output, all_turns=True)
    assert len(view["turns"]) == 1 and view["turns"][0]["turn"] == 1
    assert view["predictor"]["llm_input"] == requests[0]
    assert not view["predictor"]["receipt_warnings"]
    trajectory["predictor"]["selected_names"] = []
    with pytest.raises(ValueError, match="Selected APIs"):
        validate_receipts(trajectory, output)


def test_truncated_selector_records_policy_failure_without_actor_call(
    tmp_path, monkeypatch, config
):
    requests = predictor_fixture(monkeypatch, config, truncated=True)
    output = tmp_path / "teacher_truncated"
    trajectory = p3a.run_teacher_episode(
        config, "fake", output, {"DEEPSEEK_API_KEY": "synthetic"}
    )
    assert trajectory["status"] == "valid"
    assert (
        trajectory["reward"] == 0
        and trajectory["termination_reason"] == "invalid_prediction"
    )
    assert len(requests) == 1 and not trajectory["turns"]
    assert trajectory["usage"]["generated_tokens"] == 20
    assert build_view(output, all_turns=True)["predictor"]["format_error"]
    assert p3a.interrupted_usage(output, config["teacher"])["provider_requests"] == 1


def test_selector_uses_shared_budget(tmp_path, monkeypatch, config):
    requests = predictor_fixture(monkeypatch, config)
    config["remaining_requests"] = 1
    trajectory = p3a.run_teacher_episode(
        config, "fake", tmp_path / "teacher_limited", {"DEEPSEEK_API_KEY": "synthetic"}
    )
    assert len(requests) == 1 and trajectory["usage"]["model_turns"] == 0
    assert trajectory["termination_reason"] == "budget_exhausted"


def test_static_schema_does_not_expand_and_unknown_is_rejected():
    extra = {"type": "function", "function": {"name": "spotify__login"}}
    adapter = SimpleNamespace(
        catalogue=TOOLS,
        execute=lambda *args: ("real feedback fixture", TOOLS + [extra]),
    )
    assert (
        tool_modes.execute_with_mode(adapter, "x", "{}", TOOLS, "api_predictor")[1]
        == TOOLS
    )
    assert (
        len(
            tool_modes.execute_with_mode(
                adapter, "x", "{}", TOOLS, "dynamic_discovery"
            )[1]
        )
        == 2
    )
    with pytest.raises(ValueError, match="non-public"):
        tool_modes.select_tools(adapter, ["admin__secret"])


def test_upstream_integrity_gate_and_mode_validation(tmp_path, config):
    (tmp_path / "api_predictor.py").write_text(
        "raise RuntimeError('must never execute')"
    )
    with pytest.raises(ValueError, match="source hash"):
        tool_modes.official_predictor(tmp_path)
    config["tool_mode"] = "ground_truth"
    with pytest.raises(ValueError, match="Unknown tool"):
        p3a.validate_config(config)


def test_comparison_uses_failures_in_denominator_and_checks_pairs(tmp_path, config):
    write_json(tmp_path / "config.json", config)
    write_json(tmp_path / "task_selection.json", {"tasks": ["fake"]})
    for i, mode in enumerate(tool_modes.MODES):
        write_json(
            tmp_path / f"teacher_{i}" / "trajectory.json",
            {
                "trajectory_id": f"teacher_{i}",
                "task_id": "fake",
                "tool_mode": mode,
                "status": "valid",
                "reward": i,
                "termination_reason": "completion_claim",
                "initial_state_hash": digest("same"),
                "observed_backend": ["model", "fingerprint"],
                "worker_pid": i + 10,
                "usage": {"tool_calls": 1, "generated_tokens": 20},
            },
        )
    result = summarize_comparison(tmp_path)
    assert result["status"] == "PASS"
    assert result["metrics"]["dynamic_discovery"]["success_per_attempt"] == 0
    assert result["metrics"]["api_predictor"]["success_per_attempt"] == 1
    record_path = tmp_path / "teacher_1" / "trajectory.json"
    record = read_json(record_path)
    record["initial_state_hash"] = digest("different")
    record_path.unlink()
    write_json(record_path, record)
    assert summarize_comparison(tmp_path)["status"] == "PARTIAL"
