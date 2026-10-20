"""Software-only regression coverage for count quotas and explicit prompt variants."""

import copy
import sys
from types import SimpleNamespace

import pytest

from recoveragent import APPWORLD_REVISION, p3a
from recoveragent.demonstrations import export_dataset, verify_dataset
from recoveragent.io import read_json, write_json
from recoveragent.p3a_prompts import actor_system
from recoveragent.teacher import TeacherPolicy, normalize_message
from recoveragent.tool_modes import DEPENDENCY_CHECK, predictor_messages
from test_runner import Adapter, TOOLS
from test_teacher_data import config as teacher_config, response


@pytest.fixture
def config():
    return teacher_config.__wrapped__()


@pytest.mark.parametrize("value", [None, 1, 1000])
def test_optional_quota_validates(config, value):
    config["limits"]["max_tool_calls"] = value
    config["teacher"]["max_tool_calls_per_turn"] = value
    p3a.validate_config(config)


@pytest.mark.parametrize("value", [0, -1, False, "unlimited"])
def test_invalid_quota_rejected(config, value):
    config["limits"]["max_tool_calls"] = value
    with pytest.raises(ValueError, match="Tool count limits"):
        p3a.validate_config(config)


def test_more_than_old_64_batch_and_100_world_limit(tmp_path, monkeypatch, config):
    assert config["limits"]["max_tool_calls"] is None
    assert config["teacher"]["max_tool_calls_per_turn"] is None
    tools = [
        {
            "type": "function",
            "function": {
                "name": "synthetic__read",
                "parameters": {
                    "type": "object",
                    "properties": {"item": {"type": "integer"}},
                },
            },
        },
        *TOOLS,
    ]
    value = response()
    calls = [
        {
            "id": f"read_{i}",
            "type": "function",
            "function": {
                "name": "synthetic__read",
                "arguments": '{"item":' + str(i) + "}",
            },
        }
        for i in range(100)
    ]
    calls += value["choices"][0]["message"]["tool_calls"]
    value["choices"][0]["message"]["tool_calls"] = calls
    assert len(normalize_message(value, max_calls=None)["tool_calls"]) == 101
    with pytest.raises(ValueError, match="limit"):
        normalize_message(value, max_calls=64)
    dispatches = []

    class World(Adapter):
        versions = {"revision": APPWORLD_REVISION, "data_version": "SYNTHETIC"}

        def __init__(self, *args, **kwargs):
            assert kwargs["max_interactions"] == sys.maxsize
            super().__init__(*args, **kwargs)

        def initial_tools(self):
            return tools

        def execute(self, name, arguments, visible):
            dispatches.append((name, arguments))
            self.completed = name == "supervisor__complete_task"
            return arguments, visible

    monkeypatch.setattr(p3a, "AppWorldAdapter", World)
    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda cfg, creds: TeacherPolicy(
            cfg, creds, transport=lambda _: copy.deepcopy(value)
        ),
    )
    monkeypatch.setattr(
        p3a, "official_evaluate", lambda *args: (True, {"synthetic": True})
    )
    run = tmp_path / "run"
    write_json(run / "config.json", config)
    result = p3a.run_teacher_episode(
        config, "fake", run / "teacher_fixture", {"DEEPSEEK_API_KEY": "synthetic"}
    )
    assert result["status"] == "valid"
    assert len(dispatches) == result["usage"]["tool_calls"] == 101
    assert len(result["turns"]) == 1
    manifest = export_dataset(run, tmp_path / "data", {"fake"}, config["limits"])
    assert manifest["counts"]["accepted"] == 1
    verify_dataset(tmp_path / "data", {"fake"})


def test_prompt_states_actual_limits_and_keeps_official_predictor_distinct(config):
    config["remaining_generated_tokens"] = 8192
    text = actor_system(config)
    assert "4096" in text and "8192" in text and "450" not in text
    assert "no numeric count quota" in text
    assert "complete function names and JSON arguments" in text
    assert "already visible schema" in text
    base = [{"role": "user", "content": "synthetic public task"}]
    predictor = SimpleNamespace(build_messages=lambda *a, **kw: copy.deepcopy(base))
    assert predictor_messages(predictor, None, "official_zero_shot") == base
    enhanced = predictor_messages(predictor, None, "dependency_aware_v1")
    assert enhanced[-1]["content"] == base[-1]["content"] + "\n\n" + DEPENDENCY_CHECK
    assert base[-1]["content"] == "synthetic public task"
    config["actor_prompt_variant"] = "legacy_v1"
    assert "bounded batch" in actor_system(config)


def test_old_version_and_finite_settings_remain_readable(config):
    config["project_version"] = "0.5"
    config["limits"]["max_tool_calls"] = 40
    config["teacher"]["max_tool_calls_per_turn"] = 64
    config.pop("actor_prompt_variant")
    config.pop("predictor_prompt_variant")
    p3a.validate_config(config)
    assert "bounded batch" in actor_system(config)
    assert read_json("configs/p3a_teacher.json")["teacher"]["max_new_tokens"] == 4096
