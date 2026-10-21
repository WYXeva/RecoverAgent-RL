"""Software-only fixtures; no Teacher or real AppWorld success is claimed."""

import copy
import hashlib
import json

import pytest

from recoveragent import APPWORLD_REVISION, p3a, student
from recoveragent.answer_guard import (
    AnswerGuard,
    COMPLETE,
    PROTOCOL,
    RULES,
    public_contract,
    validate_guard_record,
)
from recoveragent.base_sft import assistant_examples
from recoveragent.demonstrations import export_dataset, verify_dataset
from recoveragent.inspect_teacher import _feedback
from recoveragent.io import write_json
from recoveragent.student import StudentSession
from recoveragent.teacher import TeacherPolicy
from test_runner import Adapter
from test_student_batch import Tokenizer, block, receipt
from test_teacher_data import config as teacher_config_fixture, response


@pytest.fixture
def config(request):
    return teacher_config_fixture.__wrapped__()


INSTRUCTION = 'What is the total cost of my electricity bills for this year? The bills are in "~/bills/" directory.'
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": COMPLETE,
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": ["number", "string"]},
                    "status": {"type": "string"},
                },
            },
        },
    }
]


def call(value, name=COMPLETE):
    return {
        "id": "test",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps({"answer": value})},
    }


@pytest.mark.parametrize(
    "instruction,kind",
    [
        (INSTRUCTION, "money"),
        ("How many albums do I own?", "number"),
        ("What is the total cost in each currency?", "unclassified"),
        ("What is the total cost? Include the currency symbol.", "unclassified"),
        ("What is the total cost and number of orders?", "unclassified"),
        ("How many orders? What are their IDs?", "unclassified"),
        ("What is the total cost range?", "unclassified"),
        ("How many percent?", "unclassified"),
        ("Find the ID 00123.", "unclassified"),
    ],
)
def test_public_contract_is_conservative(instruction, kind):
    assert public_contract(instruction)["kind"] == kind


@pytest.mark.parametrize(
    "value", ["$1,234", "10–20", "3%", "00123", "USD 12", "a $3 b $4"]
)
def test_ambiguous_strings_are_forwarded_unchanged_without_guess(value):
    guard = AnswerGuard(INSTRUCTION)
    calls = [call(value)]
    original = copy.deepcopy(calls)
    assert guard.inspect(calls) == [None]
    assert calls == original and not guard.pending


def test_exactly_one_correction_same_value_and_no_mutation():
    guard = AnswerGuard(INSTRUCTION)
    calls = [call("$368")]
    original = copy.deepcopy(calls)
    error = guard.inspect(calls)[0]
    assert error["error"] == "harness_format_error" and not error["appworld_executed"]
    assert calls == original and guard.pending
    assert guard.inspect([call(368)]) == [None]
    assert guard.correction_used and guard.correction_valid


@pytest.mark.parametrize(
    "retry",
    [[call(369)], [call("368")], [call(368, "other__tool")], [call(368)] * 2, []],
)
def test_changed_value_extra_tools_or_second_invalid_reply_stop(retry):
    guard = AnswerGuard(INSTRUCTION)
    guard.inspect([call("$368")])
    assert all(v is not None for v in guard.inspect(retry))
    assert guard.stopped and not guard.correction_valid
    with pytest.raises(ValueError, match="exhausted"):
        guard.inspect([call(368)])


def collect(tmp_path, monkeypatch, config, budget=2):
    class World(Adapter):
        versions = {"revision": APPWORLD_REVISION, "data_version": "SYNTHETIC"}
        catalogue = TOOLS

        def initial_tools(self):
            return TOOLS

        def public_task(self):
            return {"instruction": INSTRUCTION}

        def execute(self, name, arguments, tools):
            assert json.loads(arguments)["answer"] == 368
            return super().execute(name, arguments, tools)

    attempts = []

    def transport(payload):
        # The next model really sees the explicit rejection in its unchanged history.
        value = "$368"
        if attempts:
            assert (
                json.loads(payload["messages"][-1]["content"])["source"]
                == "recoveragent_harness"
            )
            value = 368
        attempts.append(value)
        result = response()
        result["choices"][0]["message"]["tool_calls"] = [call(value)]
        result["choices"][0]["message"]["tool_calls"][0]["id"] = str(len(attempts))
        return result

    config.update(
        project_version="0.10",
        actor_prompt_variant="execution_v5",
        answer_guard_protocol=PROTOCOL,
        remaining_requests=budget,
    )
    monkeypatch.setattr(p3a, "AppWorldAdapter", World)
    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda c, creds: TeacherPolicy(c, creds, transport=transport),
    )
    monkeypatch.setattr(
        p3a, "official_evaluate", lambda *a: (len(attempts) == 2, {"synthetic": True})
    )
    run = tmp_path / "run"
    write_json(run / "config.json", config)
    result = p3a.run_teacher_episode(
        config,
        "unit_task",
        run / "teacher_synthetic",
        {"DEEPSEEK_API_KEY": "unit-secret"},
    )
    return run, result


def test_teacher_guard_receipts_export_and_context_only_loss(
    tmp_path, monkeypatch, config
):
    run, t = collect(tmp_path, monkeypatch, config)
    assert t["reward"] == 1 and t["status"] == "valid"
    assert t["usage"]["provider_requests"] == 2
    assert t["usage"]["tool_requests"] == 2 and t["usage"]["tool_calls"] == 1
    assert t["usage"]["harness_rejections"] == 1
    assert [turn["supervise"] for turn in t["turns"]] == [False, True]
    assert not list((run / "teacher_synthetic/dispatches").glob("001_*"))
    export_dataset(run, tmp_path / "dataset", {"unit_task"}, config["limits"])
    assert (
        verify_dataset(tmp_path / "dataset", {"unit_task"})["counts"]["accepted"] == 1
    )
    sample = json.loads((tmp_path / "dataset/demonstrations.jsonl").read_text())

    class Template(Tokenizer):
        def apply_chat_template(self, messages, **kwargs):
            return "".join(json.dumps(m) for m in messages)

    examples = assistant_examples(sample, Template())
    assert len(examples) == 1
    row = examples[0]
    prefix = "".join(chr(x) for x in row["input_ids"][: row["target_start"]])
    assert "$368" in prefix and "harness_format_error" in prefix
    assert set(row["labels"][: row["target_start"]]) == {-100}
    assert all(x == 1 for x in row["loss_mask"][row["target_start"] :])
    for damage in ("mask", "feedback", "dispatch"):
        altered = copy.deepcopy(t)
        if damage == "mask":
            altered["turns"][0]["supervise"] = True
        elif damage == "feedback":
            altered["messages"][3]["content"] = "fake appworld success"
        else:
            altered["turns"][0]["appworld_dispatched_count"] = 1
        with pytest.raises(ValueError):
            validate_guard_record(altered)


def test_correction_does_not_increase_request_budget(tmp_path, monkeypatch, config):
    _, t = collect(tmp_path, monkeypatch, config, budget=1)
    assert t["reward"] == 0 and t["usage"]["provider_requests"] == 1
    assert t["usage"]["tool_calls"] == 0 and t["answer_guard"]["pending"]
    assert t["termination_reason"] == "budget_exhausted"


def test_student_shares_guard_and_never_dispatches_rejection(monkeypatch):
    monkeypatch.setattr(
        student,
        "TEMPLATE_SHA256",
        hashlib.sha256(Tokenizer.chat_template.encode()).hexdigest(),
    )

    class World(Adapter):
        catalogue = TOOLS

    world = World(".", "unit_task")
    session = StudentSession(
        world,
        Tokenizer(),
        [
            {"role": "system", "content": RULES},
            {"role": "user", "content": json.dumps({"instruction": INSTRUCTION})},
        ],
        TOOLS,
        tool_mode="dynamic_discovery",
        answer_guard=True,
    )
    session.step(receipt(block(COMPLETE, {"answer": "$368"})))
    assert not world.completed and world.value == 0
    session.step(receipt(block(COMPLETE, {"answer": 368})))
    assert world.completed and world.value == 1
    assert session.usage["tool_calls"] == session.usage["harness_rejections"] == 1


def test_viewer_preserves_mixed_appworld_and_harness_feedback(tmp_path):
    write_json(tmp_path / "dispatches/001_001.json", {"content": "real"})
    event = {
        "harness_verdicts": [None, {"error": "harness_format_error"}],
        "tool_results": [{"content": "real"}, {"content": "guard"}],
    }
    assert _feedback(tmp_path, "001", event, {})[0] == event["tool_results"]
