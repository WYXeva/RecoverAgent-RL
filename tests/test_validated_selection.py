"""Synthetic correctness tests, not real Teacher/environment acceptance."""

import copy
from types import SimpleNamespace

import pytest

from recoveragent import p3a, tool_modes, validated_selection as vs
from recoveragent.demonstrations import (
    validate_receipts,
    export_dataset,
    verify_dataset,
)
from recoveragent.execution_v4 import actor_system
from recoveragent.inspect_teacher import build_view
from recoveragent.io import read_json, write_json
from recoveragent.teacher import TeacherPolicy, usage_cost
from test_teacher_data import config as teacher_config, response
from test_tool_modes import predictor_fixture


@pytest.fixture
def config():
    value = teacher_config.__wrapped__()
    value.update(
        project_version="0.9",
        actor_prompt_variant="execution_v4",
        predictor_prompt_variant="dependency_examples_v2",
        selection_protocol=vs.PROTOCOL,
    )
    return value


def docs():
    def doc(required=(), outputs=None):
        return {
            "parameters": [{"name": n, "required": True} for n in required],
            "response_schemas": {"success": outputs or {"message": "string"}},
        }

    return {
        "supervisor": {
            "complete_task": doc(),
            "show_account_passwords": doc(outputs=[{"password": "string"}]),
        },
        "notes": {
            "search": doc(["access_token"]),
            "login": doc(["password"], {"access_token": "string"}),
            "refresh": doc(["access_token"], {"access_token": "string"}),
            "public": doc(),
        },
        "other": {"login": doc([], {"access_token": "string"})},
    }


def test_public_dependency_paths_no_cross_app_tokens_or_cyclic_refresh():
    c = vs.dependency_catalogue(docs())
    for names in (
        ["notes__search"],
        ["notes__search", "other__login"],
        ["notes__search", "notes__refresh"],
    ):
        assert vs.dependency_errors(names, c)[0]["public_sources"] == ["notes__login"]
    assert (
        vs.dependency_errors(["notes__search", "notes__login"], c)[0]["required_input"]
        == "password"
    )
    assert not vs.dependency_errors(
        ["notes__search", "notes__login", "supervisor__show_account_passwords"], c
    )
    assert not vs.dependency_errors(["notes__public"], c)
    c["notes__alternative"] = {"required": [], "returns": ["access_token"]}
    assert not vs.dependency_errors(["notes__search", "notes__alternative"], c)


def setup_selector(monkeypatch, config, texts):
    public_docs = docs()
    c = vs.dependency_catalogue(public_docs)
    tools = [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": "public fixture",
                "parameters": {
                    "type": "object",
                    "properties": {k: {"type": "string"} for k in d["required"]},
                    "required": d["required"],
                },
            },
        }
        for n, d in c.items()
    ]

    class Predictor:
        def build_messages(self, task, include_cache_control):
            return [
                {"role": "system", "content": "public catalogue"},
                {"role": "user", "content": "public task"},
            ]

        def predicted_output_to_apis(self, task, text):
            names = [
                n.replace(".", "__")
                for n in text.splitlines()
                if n.replace(".", "__") in c
            ]
            if "supervisor__complete_task" not in names:
                names.insert(0, "supervisor__complete_task")
            return list(dict.fromkeys(names))[:20]

    requests = []

    def transport(payload):
        requests.append(copy.deepcopy(payload))
        result = response()
        result["choices"] = [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": texts[len(requests) - 1]},
            }
        ]
        return result

    monkeypatch.setattr(tool_modes, "official_predictor", lambda *a: Predictor())
    config["tool_mode"] = "api_predictor"
    adapter = SimpleNamespace(
        catalogue=tools,
        world=SimpleNamespace(task=SimpleNamespace(api_docs=public_docs)),
    )
    usage = {
        k: 0
        for k in (
            "provider_requests",
            "generated_tokens",
            "prompt_tokens",
            "cached_prompt_tokens",
            "usage_priced_cost",
            "unknown_cost_reservation",
            "unknown_generation_reservation",
        )
    }
    trajectory = {
        "teacher": config["teacher"],
        "tool_mode": "api_predictor",
        "selection_protocol": vs.PROTOCOL,
        "predictor_prompt_variant": "dependency_examples_v2",
        "turns": [],
    }
    return (
        adapter,
        TeacherPolicy(config, {"DEEPSEEK_API_KEY": "synthetic"}, transport),
        usage,
        trajectory,
        requests,
    )


def test_one_correction_receipts_shared_budget_and_no_actor_leak(
    tmp_path, monkeypatch, config
):
    texts = [
        "notes.search",
        "notes.search\nnotes.login\nsupervisor.show_account_passwords",
    ]
    adapter, policy, usage, t, requests = setup_selector(monkeypatch, config, texts)
    tools, ok = vs.initialize_tools(adapter, policy, config, tmp_path, usage, t)
    assert ok and usage["provider_requests"] == 2 and usage["generated_tokens"] == 40
    assert requests[1]["messages"][-2]["content"] == texts[0]
    assert "PUBLIC SCHEMA DEPENDENCY" in requests[1]["messages"][-1]["content"]
    t["initial_tools"] = tools
    vs.validate_predictor_receipts(t, tmp_path)
    t["messages"], t["usage"] = [], {**usage, "tool_calls": 0}
    validate_receipts(t, tmp_path)
    write_json(tmp_path / "trajectory.json", t)
    view = build_view(tmp_path, all_turns=True)
    assert len(view["predictor"]["corrections"]) == 1
    assert not view["predictor"]["corrections"][0]["receipt_warnings"]
    assert not view["turns"]
    assert p3a.interrupted_usage(tmp_path, config["teacher"])["provider_requests"] == 2
    t["predictor"]["attempts"][0]["dependency_errors"] = []
    with pytest.raises(ValueError, match="dependency evidence"):
        validate_receipts(t, tmp_path)


@pytest.mark.parametrize("budget,second", [(1, "notes.search"), (80, "notes.search")])
def test_invalid_dependency_stops_without_actor_or_silent_tool_addition(
    tmp_path, monkeypatch, config, budget, second
):
    config["remaining_requests"] = budget
    adapter, policy, usage, t, requests = setup_selector(
        monkeypatch, config, ["notes.search", second]
    )
    tools, ok = vs.initialize_tools(adapter, policy, config, tmp_path, usage, t)
    assert not ok and tools == [] and len(requests) == min(budget, 2)
    assert "notes__login" not in t["predictor"]["selected_names"]
    assert t["predictor"]["format_error"]


def test_export_roundtrip_v4_single_prediction(tmp_path, monkeypatch, config):
    predictor_fixture(monkeypatch, config)
    # Existing synthetic world has only completion; supply its public docs schema.
    base = p3a.AppWorldAdapter

    class World(base):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.world.task.api_docs["supervisor"]["complete_task"] = docs()[
                "supervisor"
            ]["complete_task"]

    monkeypatch.setattr(p3a, "AppWorldAdapter", World)
    run = tmp_path / "run"
    write_json(run / "config.json", config)
    t = p3a.run_teacher_episode(
        config, "fake", run / "teacher_synthetic", {"DEEPSEEK_API_KEY": "synthetic"}
    )
    assert t["status"] == "valid" and t["reward"] == 1
    assert t["usage"]["provider_requests"] == 2
    export_dataset(run, tmp_path / "data", {"fake"}, config["limits"])
    assert verify_dataset(tmp_path / "data", {"fake"})["counts"]["accepted"] == 1
    assert (
        read_json(run / "teacher_synthetic/trajectory.json")["selection_protocol"]
        == vs.PROTOCOL
    )
    view = build_view(run / "teacher_synthetic", all_turns=True)
    assert len(view["turns"]) == 1 and not view["turns"][0]["receipt_warnings"]


@pytest.mark.parametrize("limit", ["tokens", "cost"])
def test_correction_cannot_exceed_shared_tokens_or_cost(
    tmp_path, monkeypatch, config, limit
):
    if limit == "tokens":
        config["remaining_generated_tokens"] = 20
    else:
        # Enough for the first conservative reservation, not another after usage.
        config["remaining_cost"] = usage_cost(
            {
                "prompt_tokens": config["teacher"]["billing_context_tokens"],
                "generated_tokens": 4096,
                "cached_prompt_tokens": 0,
            },
            config["teacher"]["pricing"],
        )
    adapter, policy, usage, t, requests = setup_selector(
        monkeypatch, config, ["notes.search"]
    )
    tools, ok = vs.initialize_tools(adapter, policy, config, tmp_path, usage, t)
    assert not ok and tools == [] and len(requests) == 1
    assert t["predictor"]["format_error"] == "dependency_correction_budget_exhausted"
    assert usage["unknown_cost_reservation"] == 0


def test_correction_output_allowance_is_remaining_total(tmp_path, monkeypatch, config):
    config["remaining_generated_tokens"] = 40
    adapter, policy, usage, t, requests = setup_selector(
        monkeypatch,
        config,
        [
            "notes.search",
            "notes.search\nnotes.login\nsupervisor.show_account_passwords",
        ],
    )
    tools, ok = vs.initialize_tools(adapter, policy, config, tmp_path, usage, t)
    assert ok and [r["max_tokens"] for r in requests] == [40, 20]
    t["initial_tools"] = tools
    vs.validate_predictor_receipts(t, tmp_path)
    t["predictor"]["usage"]["generated_tokens"] -= 1
    with pytest.raises(ValueError, match="accounting"):
        vs.validate_predictor_receipts(t, tmp_path)


def test_v4_prompts_and_protocol_validation(config):
    system = actor_system(config)
    assert "OMIT the answer key entirely" in system and "N is not a literal" in system
    assert "no invented answer for an action-only task" not in system
    assert "Expand every relevant entry of nested lists" in system
    assert "NEXT model request" in system
    config["tool_mode"] = "api_predictor"
    assert "schemas are fixed" in actor_system(config)
    p3a.validate_config(config)
    config["actor_prompt_variant"] = "execution_v3"
    with pytest.raises(ValueError, match="v0.9"):
        p3a.validate_config(config)


def test_view_preserves_unanswered_correction(tmp_path):
    message = [{"role": "user", "content": "synthetic public task"}]
    request = {"messages": message, "max_tokens": 4096}
    write_json(tmp_path / "requests/000.request.json", request)
    write_json(tmp_path / "requests/000-correction.request.json", request)
    write_json(
        tmp_path / "trajectory.json",
        {
            "predictor": {
                "protocol": vs.PROTOCOL,
                "attempts": [],
                "usage": {},
                "selected_names": [],
            }
        },
    )
    view = build_view(tmp_path, all_turns=True)
    correction = view["predictor"]["corrections"][0]
    assert correction["llm_input"] == request
    assert correction["receipt_warnings"] == ["response_missing"]
