"""Software tests use a tiny fake state machine; they do not certify AppWorld."""

import copy
from pathlib import Path

import pytest
from jsonschema import ValidationError

from recoveragent.adapter import call_code
from recoveragent.buffer import FailureBuffer
from recoveragent.cli import group_report, validate_config
from recoveragent.io import digest, read_json, validate_history, write_json
from recoveragent.policy import parse_completion
from recoveragent.state import capture, inspect_snapshot, restore

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "notes__write",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        },
    }
]
MESSAGES = [{"role": "user", "content": "test"}]


class FakeAdapter:
    task_id = "unit_task"
    versions = {"mock": True}

    def __init__(self):
        self.value = 1

    def export_state(self, state_id, destination):
        assert state_id
        write_json(Path(destination) / "state.json", self.value)

    def import_state(self, source, state_id):
        self.value = read_json(Path(source) / "state.json")

    def fingerprint(self):
        return digest(self.value)

    def runtime(self):
        return {"counter": 0}

    def restore_runtime(self, runtime):
        assert runtime == self.runtime()


@pytest.fixture
def snapshot(tmp_path):
    return capture(
        FakeAdapter(),
        tmp_path / "source",
        MESSAGES,
        TOOLS,
        "post_action",
        {"mock": True},
    )


def test_state_export_restore_and_source_immutable(snapshot):
    adapter = FakeAdapter()
    adapter.value = 90
    restore(adapter, snapshot)
    assert adapter.value == 1
    adapter.value = 20
    assert inspect_snapshot(snapshot)[0]["logical_state_hash"] == digest(1)
    restore(adapter, snapshot)
    assert adapter.value == 1


@pytest.mark.parametrize(
    "file", ["context.json", "runtime.json", "dbs/state.json", "manifest.json"]
)
def test_snapshot_tampering_rejected(snapshot, file):
    path = Path(snapshot) / file
    path.write_text("{}")
    with pytest.raises((ValueError, KeyError)):
        inspect_snapshot(snapshot)


def test_task_version_mismatch_rejected(snapshot):
    adapter = FakeAdapter()
    adapter.versions = {"mock": "different"}
    with pytest.raises(ValueError):
        restore(adapter, snapshot)


def test_pending_call_cannot_be_snapshot(tmp_path):
    message = parse_completion(
        '<tool_call>{"name":"notes__write","arguments":{"value":3}}</tool_call>', "c"
    )
    with pytest.raises(ValueError):
        capture(
            FakeAdapter(),
            tmp_path / "bad",
            MESSAGES + [message],
            TOOLS,
            "post_action",
            {},
        )
    history = MESSAGES + [
        message,
        {"role": "tool", "tool_call_id": "c", "content": "done"},
    ]
    validate_history(history)
    with pytest.raises(ValueError):
        validate_history(history + [message])


@pytest.mark.parametrize(
    "name,args",
    [
        ("evaluate", {}),
        ("notes__write;evil", {}),
        ("notes__write", {"value": "text"}),
        ("notes__write", {"value": 1, "extra": "bad"}),
    ],
)
def test_tool_allowlist_and_parameter_types(name, args):
    with pytest.raises((ValueError, ValidationError)):
        call_code(name, args, TOOLS)


def test_arguments_are_literals_not_executable():
    tools = copy.deepcopy(TOOLS)
    tools[0]["function"]["parameters"]["properties"]["value"]["type"] = "string"
    attack = "'); __import__('os').system('false'); ('"
    code, _ = call_code("notes__write", {"value": attack}, tools)
    calls = []
    exec(
        code,
        {
            "notes__write": lambda **kwargs: calls.append(kwargs),
            "print": lambda *a: None,
        },
    )
    assert calls == [{"value": attack}]


@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "<tool_call>{}</tool_call>",
        '<tool_call>{"name":"x","arguments":{}}</tool_call>' * 2,
    ],
)
def test_bad_completion_is_not_partially_executed(text):
    with pytest.raises(ValueError):
        parse_completion(text, "id")


def failed_trajectory(tmp_path):
    path = tmp_path / "evaluation.json"
    write_json(
        path, {"task_id": "unit_task", "trajectory_id": "unit_1", "success": False}
    )
    return {
        "task_id": "unit_task",
        "trajectory_id": "unit_1",
        "split": "train",
        "scenario_id": "unit",
        "evaluation_ref": str(path),
        "status": "valid",
        "natural": True,
        "policy": {"test_only": True},
        "termination_reason": "budget_exhausted",
    }


def test_buffer_roundtrip_dedupe(snapshot, tmp_path):
    trajectory = failed_trajectory(tmp_path)
    buffer = FailureBuffer(tmp_path / "buffer", 1)
    first = buffer.add(trajectory, snapshot)
    assert buffer.add(trajectory, snapshot) == first
    assert FailureBuffer(tmp_path / "buffer").records() == [first]


@pytest.mark.parametrize(
    "field,value",
    [
        ("split", "dev"),
        ("split", "test_normal"),
        ("status", "infrastructure_error"),
        ("natural", False),
    ],
)
def test_invalid_failure_cannot_enter_buffer(snapshot, tmp_path, field, value):
    trajectory = failed_trajectory(tmp_path)
    trajectory[field] = value
    with pytest.raises(ValueError):
        FailureBuffer(tmp_path / "buffer").add(trajectory, snapshot)


def test_identical_rewards_can_pass_infrastructure(snapshot):
    config = {
        "policy_identity": {"mock": True},
        "continuation_prompt": "continue",
        "recovery": {"max": 1},
    }
    branches = [
        {
            "status": "valid",
            "reward": 0,
            "start": {"id": 1},
            "trajectory_id": str(i),
            "seed": i,
            "worker_pid": i + 100,
        }
        for i in range(4)
    ]
    report = group_report(branches, {"id": 1}, snapshot, config)
    assert report["infrastructure_status"] == "PASS"
    assert report["reward_variance_available"] is False
    assert report["grpo_ready"] is False
    branches[1]["start"] = {"id": 2}
    assert (
        group_report(branches, {"id": 1}, snapshot, config)["infrastructure_status"]
        == "PARTIAL"
    )


def test_config_rejects_training_and_budget_overruns():
    config = read_json(Path(__file__).parents[1] / "configs/p0_p2.json")
    validate_config(config)
    for key, value in [
        ("training_enabled", True),
        ("max_infrastructure_retries", 2),
        ("total_generated_tokens", 150001),
    ]:
        invalid = dict(config, **{key: value})
        with pytest.raises(ValueError):
            validate_config(invalid)
