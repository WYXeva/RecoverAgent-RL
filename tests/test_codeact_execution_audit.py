"""Real CodeAct loop with synthetic tool returns; no GPU/Teacher requests."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_codeact_reboot import response

from recoveragent.codeact import episode, execution_audit as audit, protocol
from recoveragent.codeact.agent_evaluation import judge_one
from recoveragent.io import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


def cfg():
    result = read_json(ROOT / "configs/codeact_test111_20261122.json")
    result["limits"]["max_requests"] = 4
    return result


class World:
    def __init__(self, observation="ok"):
        self.task = SimpleNamespace(
            instruction="Synthetic public task", supervisor={}, datetime="2020-01-01"
        )
        self.shell = SimpleNamespace(user_ns={})
        self.models = SimpleNamespace(model_hashes=lambda: {})
        self.calls = []
        self.observation = observation
        self.done = False

    def execute(self, code):
        self.calls.append(code)
        self.done = code == "done"
        return "done" if self.done else self.observation

    def task_completed(self):
        return self.done


class Client:
    def __init__(self, replies, config):
        self.replies = replies
        self.messages = []
        self.usage = {"requests": 0, "generated_tokens": 0, "prompt_tokens": 0}
        self.execution = None

    def generate(self, messages, directory, index, remaining):
        self.messages.append(copy.deepcopy(messages))
        self.usage["requests"] += 1
        self.usage["generated_tokens"] += 1
        answer = copy.deepcopy(self.replies[min(index, len(self.replies) - 1)])
        for n, call in enumerate(answer["choices"][0]["message"].get("tool_calls", [])):
            if "id" in call:
                call["id"] = f"call_{index}_{n}"
        return answer

    def remember(self, *args):
        pass


def save(directory, loop, success=False):
    trajectory = {**loop, "task_id": "synthetic"}
    write_json(directory / "trajectory.json", trajectory)
    write_json(directory / "api_calls.json", [])
    write_json(
        directory / "result.json",
        {
            "success": success,
            "valid": True,
            "termination": loop["termination"],
            "usage": loop["usage"],
            "seconds": 1,
            "api_counts": {"business": 0},
        },
    )
    return audit.analyze(directory, "S0", "synthetic")


@pytest.mark.parametrize(
    "case,expected",
    [
        ("wrong_name", "wrong_outer_tool"),
        ("multiple", "multiple_tool_calls"),
        ("bad_json", "invalid_arguments_json"),
        ("missing_code", "missing_code"),
        ("empty", "empty_code"),
        ("wrong_type", "code_not_string"),
        ("no_call", "no_appworld_call"),
        ("missing_structure", "missing_tool_structure"),
    ],
)
def test_format_rejection_never_executes_and_counted_once(case, expected, tmp_path):
    bad = response("print(1)")
    msg = bad["choices"][0]["message"]
    call = msg["tool_calls"][0]
    if case == "wrong_name":
        call["function"]["name"] = "gmail"
    elif case == "multiple":
        msg["tool_calls"].append(copy.deepcopy(call))
    elif case == "bad_json":
        call["function"]["arguments"] = "{"
    elif case == "missing_code":
        call["function"]["arguments"] = "{}"
    elif case == "empty":
        call["function"]["arguments"] = '{"code":" "}'
    elif case == "wrong_type":
        call["function"]["arguments"] = '{"code":12}'
    elif case == "no_call":
        msg["tool_calls"] = []
    else:
        call.pop("id")
    world = World()
    client = Client([bad], cfg())
    loop = episode.loop(world, client, cfg(), tmp_path)
    assert world.calls == [] and loop["termination"] == "format_corrections_exhausted"
    assert (
        len(client.messages) == 3
        and "not executed" in client.messages[1][-1]["content"]
    )
    metrics = save(tmp_path, loop)
    assert metrics["format_rejections"] == 3 and metrics["replies"] == 3
    assert all(expected in e["types"] for e in metrics["events"])


@pytest.mark.parametrize(
    "observation,kind,api",
    [
        ("Execution failed. SyntaxError: invalid syntax", "SyntaxError", None),
        ("Execution failed. NameError: missing", "NameError", None),
        (
            "Execution failed. Exception: Response status code is 422: missing required parameter",
            "OtherExecutionError",
            "missing_api_parameter",
        ),
        (
            "Execution failed. Exception: Response status code is 401: invalid access token",
            "OtherExecutionError",
            "authentication_error",
        ),
        (
            "Execution failed. Exception: Response status code is 404: object missing",
            "OtherExecutionError",
            "resource_not_found",
        ),
    ],
)
def test_execution_error_is_not_format_rejection(observation, kind, api, tmp_path):
    world = World(observation)
    client = Client(
        [response("apis.gmail.show_emails()", 0), response("done", 1)], cfg()
    )
    loop = episode.loop(world, client, cfg(), tmp_path)
    metrics = save(tmp_path, loop)
    assert (
        len(world.calls) == 2
        and loop["format_rejections"] == 0
        and loop["termination"] == "task_completed"
    )
    assert metrics["code_errors"] == 1 and metrics["events"][0]["types"] == [kind]
    if api:
        assert metrics["events"][1]["types"] == [api]
    assert (
        metrics["business_api_errors"] is None
    )  # Historical catches cannot be invented.


@pytest.mark.parametrize("success", [True, False])
def test_completed_submission_does_not_override_official_result(success, tmp_path):
    world = World()
    loop = episode.loop(world, Client([response("done")], cfg()), cfg(), tmp_path)
    metrics = save(tmp_path, loop, success)
    assert loop["termination"] == "task_completed" and metrics["success"] is success
    assert not metrics["code_errors"] and not metrics["format_rejections"]


@pytest.mark.parametrize(
    "budget,expected",
    [
        ("requests", "request_limit"),
        ("tokens", "token_limit"),
        ("wall", "wall_limit"),
        ("client", "client_budget"),
        ("repeat", "repeated_error_limit"),
    ],
)
def test_budget_and_repeat_termination(budget, expected, tmp_path, monkeypatch):
    config = cfg()
    world = World()
    client = Client([response("print(1)")], config)
    if budget == "tokens":
        config["limits"]["max_generated_tokens"] = 1
    if budget == "wall":
        config["limits"]["wall_seconds"] = 0
    if budget == "client":

        def exhausted(*args):
            raise protocol.BudgetExhausted("context exhausted")

        client.generate = exhausted
    if budget == "repeat":
        from recoveragent.codeact.progress import execution_identity

        config["limits"]["max_requests"] = 8
        client.execution = execution_identity(config)
        world.observation = "Execution failed. NameError: missing"
    loop = episode.loop(world, client, config, tmp_path)
    assert loop["termination"] == expected
    assert loop["format_rejections"] == 0
    if budget == "repeat":
        assert len(world.calls) == 5
    if budget in ("wall", "client"):
        assert world.calls == []


def test_redaction_does_not_drop_ids_and_public_input_excludes_hidden(tmp_path):
    trajectory = {
        "messages": [{"role": "user", "content": "Count objects 27 and 28"}],
        "actions": [
            {
                "request_index": 0,
                "code": "apis.x.y(access_token='SECRET',object_id=27)",
                "observation": "{'password': 'PASS', 'id': 28}",
            }
        ],
        "hidden_evaluator": "MUST NEVER ENTER JUDGE",
    }
    write_json(tmp_path / "trajectory.json", trajectory)
    data = audit.judge_input(tmp_path)
    encoded = json.dumps(data)
    assert (
        "SECRET" not in encoded
        and "PASS" not in encoded
        and "MUST NEVER" not in encoded
    )
    assert "27" in encoded and "28" in encoded


def test_rubric_structure_and_no_success_reward():
    value = {
        k: {"score": 2, "reason": "synthetic evidence", "evidence_steps": [0]}
        for k in audit.DIMENSIONS
    }
    assert audit.validate_judgment(value, 1) == value
    value["tool_usage"]["score"] = True
    with pytest.raises(ValueError):
        audit.validate_judgment(value, 1)


def test_judge_real_request_and_parsing_mock_network(tmp_path, monkeypatch):
    import threading
    from recoveragent.codeact import agent_evaluation as ae

    config = read_json(ROOT / "configs/codeact_execution_rubric_20261122.json")
    write_json(
        tmp_path / "episode/trajectory.json",
        {
            "messages": [{"role": "user", "content": "Count"}],
            "actions": [{"request_index": 0, "code": "print(1)", "observation": "1"}],
        },
    )
    seen = []
    result = {
        k: {"score": 1, "reason": "mock only", "evidence_steps": [0]}
        for k in audit.DIMENSIONS
    }

    def transport(self, payload):
        seen.append(payload)
        return {
            "model": config["teacher"]["model"],
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(result),
                        "reasoning_content": "private thought",
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 50},
        }

    monkeypatch.setattr(ae.TeacherPolicy, "_post", transport)
    monkeypatch.setattr(ae, "receipt", lambda *args: {"within_budget": True})
    item = {"directory": str(tmp_path / "episode"), "split": "dev24", "model": "S0"}
    value = judge_one(
        item,
        config,
        {"DEEPSEEK_API_KEY": "synthetic"},
        tmp_path / "judges",
        threading.Event(),
    )
    assert value["status"] == "valid" and value["total"] == 5 and len(seen) == 1
    assert (
        seen[0]["thinking"] == {"type": "enabled"}
        and seen[0]["reasoning_effort"] == "high"
    )
    assert seen[0]["max_tokens"] == 8192 and "temperature" not in seen[0]
    assert seen[0]["messages"][0]["content"] == audit.JUDGE
    judge_one(
        item,
        config,
        {"DEEPSEEK_API_KEY": "synthetic"},
        tmp_path / "judges",
        threading.Event(),
    )
    assert len(seen) == 1
