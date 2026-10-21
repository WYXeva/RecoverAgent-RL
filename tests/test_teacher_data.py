"""Synthetic transport/world fixtures only; these tests never certify a real Teacher."""

import copy
import json
from pathlib import Path

import pytest

from recoveragent import APPWORLD_REVISION, p3a
from recoveragent.base_sft import pad_examples
from recoveragent.demonstrations import export_dataset, verify_dataset
from recoveragent.io import read_json, write_json
from recoveragent.teacher import (
    TeacherBlocked,
    TeacherPolicy,
    TeacherTransportError,
    normalize_message,
    preflight,
    read_credentials,
)
from test_runner import Adapter, TOOLS


@pytest.fixture
def config():
    value = read_json(Path(__file__).parents[1] / "configs/p3a_teacher.json")
    # Synthetic prices and approval are confined to tests and tmp_path.
    value["authorization"] = {
        "approved": True,
        "reference": "SYNTHETIC_TEST_ONLY",
        "currency": "CNY",
        "max_cost": 5,
    }
    value["teacher"]["pricing"] = {
        "currency": "CNY",
        "input_per_million": 1,
        "cached_input_per_million": 0.1,
        "output_per_million": 2,
        "source": "synthetic test",
        "checked_at": "synthetic",
    }
    value.update(
        remaining_requests=80, remaining_generated_tokens=32768, remaining_cost=5
    )
    return value


def response():
    return {
        "model": "deepseek-flash",
        "system_fingerprint": "synthetic_backend",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "synthetic_call_1",
                            "type": "function",
                            "function": {
                                "name": "supervisor__complete_task",
                                "arguments": '{"answer":"synthetic"}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 50,
        },
    }


def install_fixture(monkeypatch, config, provider_response=None, transport_error=False):
    class FakeWorld(Adapter):
        versions = {"revision": APPWORLD_REVISION, "data_version": "SYNTHETIC"}

    def transport(payload):
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["parallel_tool_calls"] is False
        assert payload["tools"] == TOOLS
        assert all(
            "evaluation" not in m and "reward" not in m for m in payload["messages"]
        )
        if transport_error:
            raise TeacherTransportError("synthetic timeout")
        return copy.deepcopy(provider_response or response())

    monkeypatch.setattr(p3a, "AppWorldAdapter", FakeWorld)
    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda cfg, creds: TeacherPolicy(cfg, creds, transport=transport),
    )
    monkeypatch.setattr(
        p3a, "official_evaluate", lambda *args: (True, {"synthetic": True})
    )


def collect_fixture(tmp_path, monkeypatch, config, **kwargs):
    install_fixture(monkeypatch, config, **kwargs)
    run = tmp_path / "run"
    write_json(run / "config.json", config)
    result = p3a.run_teacher_episode(
        config,
        "unit_task",
        run / "teacher_synthetic",
        {"DEEPSEEK_API_KEY": "unit-secret"},
    )
    return run, result


def test_no_authorization_never_calls_provider(config):
    config["authorization"]["approved"] = False
    invoked = []
    with pytest.raises(TeacherBlocked):
        TeacherPolicy(
            config,
            {"DEEPSEEK_API_KEY": "unit-secret"},
            transport=lambda p: invoked.append(p),
        )
    assert not invoked
    assert "PAID_TEACHER_NOT_AUTHORIZED" in preflight(config, {})["reasons"]


def test_formal_source_inventory_deduplicates_packages_and_rejects_tamper(
    tmp_path, monkeypatch, config
):
    import shutil
    from recoveragent.dataset_sources import scan

    run, result = collect_fixture(tmp_path, monkeypatch, config)
    one, two = tmp_path / "one", tmp_path / "two"
    export_dataset(run, one, {"unit_task"}, config["limits"])
    shutil.copytree(one, two)
    pool = {"unit_task": result["scenario_id"]}
    report = scan([one, two], pool, tmp_path / "audit")
    assert report["status"] == "PASS"
    assert report["package_entries"] == 2
    assert report["unique_qualified_trajectories"] == report["unique_attempts"] == 1
    assert report["costs_by_currency"]["CNY"]["provider_requests"] == 1
    with (two / "demonstrations.jsonl").open("a") as stream:
        stream.write("{}\n")
    bad = scan([one, two], pool, tmp_path / "tampered_audit")
    assert bad["status"] == "PARTIAL" and len(bad["source_errors"]) == 1


@pytest.mark.parametrize("bad_price", [None, -1, float("nan"), float("inf")])
def test_unverified_price_blocks(config, bad_price):
    config["teacher"]["pricing"]["input_per_million"] = bad_price
    assert preflight(config, {"DEEPSEEK_API_KEY": "unit-secret"})["status"] == "BLOCKED"


def test_dotenv_is_data_not_shell(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("DEEPSEEK_API_KEY='literal-$(never-run)'\nDEEPSEEK_MODEL=one\n")
    monkeypatch.setenv("DEEPSEEK_MODEL", "two")
    values = read_credentials(path)
    assert values["DEEPSEEK_API_KEY"] == "literal-$(never-run)"
    assert values["DEEPSEEK_MODEL"] == "two"


@pytest.mark.parametrize("mutation", ["batch", "truncated", "bad_json", "reasoning"])
def test_native_protocol_rejects_invalid_completion(mutation):
    value = response()
    choice = value["choices"][0]
    if mutation == "batch":
        choice["message"]["tool_calls"] *= 2
    elif mutation == "truncated":
        choice["finish_reason"] = "length"
    elif mutation == "bad_json":
        choice["message"]["tool_calls"][0]["function"]["arguments"] = "not json"
    else:
        choice["message"]["reasoning_content"] = "unexpected"
    with pytest.raises(ValueError):
        normalize_message(value)


def test_plain_assistant_text_is_not_mislabeled_json_error():
    value = response()
    value["choices"][0] = {
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": "synthetic final"},
    }
    assert normalize_message(value) == {
        "role": "assistant",
        "content": "synthetic final",
    }


def test_teacher_normal_roundtrip_and_portability(tmp_path, monkeypatch, config):
    run, result = collect_fixture(tmp_path, monkeypatch, config)
    assert result["status"] == "valid" and result["reward"] == 1
    assert (
        result["usage"]["provider_requests"] == 1 and result["usage"]["tool_calls"] == 1
    )
    assert result["usage"]["usage_priced_cost"] == pytest.approx(0.000095)
    assert (
        result["actor_role"] == "teacher" and result["start_kind"] == "original_initial"
    )
    assert (run / "teacher_synthetic" / "terminal" / "manifest.json").exists()
    manifest = export_dataset(
        run, tmp_path / "dataset", {"unit_task"}, config["limits"]
    )
    assert manifest["counts"]["accepted"] == 1
    assert (
        verify_dataset(tmp_path / "dataset", {"unit_task"})["dataset_id"]
        == manifest["dataset_id"]
    )
    import shutil

    shutil.copytree(
        tmp_path / "dataset", tmp_path / "different_host_layout" / "dataset"
    )
    assert verify_dataset(
        tmp_path / "different_host_layout" / "dataset", {"unit_task"}
    )["sample_order"] == ["teacher_synthetic"]


def test_unknown_transport_cost_is_reserved_and_not_success(
    tmp_path, monkeypatch, config
):
    run, result = collect_fixture(tmp_path, monkeypatch, config, transport_error=True)
    assert result["status"] == "infrastructure_error" and result["reward"] is None
    assert result["usage"]["provider_requests"] == 1
    assert result["usage"]["generated_tokens"] == 0
    assert result["usage"]["unknown_cost_reservation"] > 0
    manifest = export_dataset(
        run, tmp_path / "dataset", {"unit_task"}, config["limits"]
    )
    assert manifest["counts"]["accepted"] == 0 and manifest["counts"]["attempted"] == 1
    assert manifest["counts"]["infrastructure_errors"] == 1


def test_truncation_retains_real_usage_but_cannot_enter_dataset(
    tmp_path, monkeypatch, config
):
    value = response()
    value["choices"][0]["finish_reason"] = "length"
    run, result = collect_fixture(
        tmp_path, monkeypatch, config, provider_response=value
    )
    assert result["usage"]["generated_tokens"] == 20
    assert result["usage"]["tool_calls"] == 0
    manifest = export_dataset(
        run, tmp_path / "dataset", {"unit_task"}, config["limits"]
    )
    assert manifest["counts"]["accepted"] == 0
    reasons = read_json(tmp_path / "dataset" / "attempts.json")[0]["reasons"]
    assert "invalid_call_format" in reasons


def test_unaffordable_request_stops_before_transport(tmp_path, monkeypatch, config):
    config["remaining_cost"] = 0.00001
    _, result = collect_fixture(tmp_path, monkeypatch, config)
    assert result["usage"]["provider_requests"] == 0
    assert result["termination_reason"] == "cost_budget_exhausted"


def test_student_or_wrong_split_cannot_be_teacher(tmp_path, monkeypatch, config):
    run, _ = collect_fixture(tmp_path, monkeypatch, config)
    path = run / "teacher_synthetic" / "trajectory.json"
    value = read_json(path)
    value.update(actor_role="student", split="dev")
    path.write_text(json.dumps(value))
    manifest = export_dataset(
        run, tmp_path / "dataset", {"unit_task"}, config["limits"]
    )
    assert manifest["counts"]["accepted"] == 0


def test_file_tamper_and_wrong_train_membership_detected(tmp_path, monkeypatch, config):
    run, _ = collect_fixture(tmp_path, monkeypatch, config)
    export_dataset(run, tmp_path / "dataset", {"unit_task"}, config["limits"])
    with pytest.raises(ValueError):
        verify_dataset(tmp_path / "dataset", {"some_other_train_task"})
    with (tmp_path / "dataset" / "demonstrations.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="files"):
        verify_dataset(tmp_path / "dataset", {"unit_task"})


def test_padding_never_becomes_training_target():
    examples = [
        {
            "input_ids": [1, 2],
            "labels": [-100, 2],
            "attention_mask": [1, 1],
            "loss_mask": [0, 1],
        },
        {"input_ids": [1], "labels": [1], "attention_mask": [1], "loss_mask": [1]},
    ]
    result = pad_examples(examples, 999)
    assert result[1]["labels"] == [1, -100] and result[1]["attention_mask"] == [1, 0]


def test_evaluation_runs_after_terminal_is_saved(tmp_path, monkeypatch, config):
    install_fixture(monkeypatch, config)
    output = tmp_path / "teacher_synthetic"

    def evaluate(*args):
        assert (output / "terminal" / "manifest.json").exists()
        assert (output / "terminal" / "context.json").exists()
        return True, {"synthetic": True}

    monkeypatch.setattr(p3a, "official_evaluate", evaluate)
    result = p3a.run_teacher_episode(
        config, "unit_task", output, {"DEEPSEEK_API_KEY": "unit-secret"}
    )
    assert result["status"] == "valid"


def test_complete_legal_history_required_and_ids_cannot_repeat():
    from recoveragent.demonstrations import FORMAT, validate_demo
    from recoveragent.io import digest

    message = normalize_message(response())
    messages = [
        {"role": "system", "content": "synthetic"},
        {"role": "user", "content": "synthetic"},
        message,
        {
            "role": "tool",
            "name": "supervisor__complete_task",
            "tool_call_id": "wrong_id",
            "content": "synthetic",
        },
    ]
    value = {
        "kind": FORMAT,
        "split": "train",
        "actor_role": "teacher",
        "start_kind": "original_initial",
        "messages": messages,
        "turns": [
            {"assistant_index": 2, "history_hash": digest(messages[:2]), "tools": TOOLS}
        ],
    }
    with pytest.raises(ValueError, match="Unmatched"):
        validate_demo(value)
    messages[3]["tool_call_id"] = "synthetic_call_1"
    validate_demo(value)
    messages.extend(copy.deepcopy(messages[2:]))
    with pytest.raises(ValueError, match="Duplicate"):
        validate_demo(value)


def test_reject_sft_preparation_without_real_verified_samples(
    tmp_path, monkeypatch, config
):
    from recoveragent.base_sft import prepare
    from recoveragent.demonstrations import sha256

    run = tmp_path / "empty_run"
    write_json(run / "config.json", config)
    manifest = export_dataset(
        run, tmp_path / "empty_dataset", {"unit_task"}, config["limits"]
    )
    assert manifest["dataset_status"] == "EMPTY_NO_TRAINING_DATA"
    dataset = tmp_path / "empty_dataset"
    expected = sha256(dataset / "manifest.json")
    write_json(
        tmp_path / "empty_dataset.verified.json",
        {
            "manifest_sha256": expected,
            "dataset_id": manifest["dataset_id"],
            "sample_order_hash": manifest["sample_order_hash"],
            "official_train_membership_checked": True,
        },
    )
    with pytest.raises(ValueError, match="No verified Teacher"):
        prepare(dataset, tmp_path / "sft_data", expected)
    assert not (tmp_path / "sft_data").exists()


def batch_response():
    value = response()
    first = copy.deepcopy(value["choices"][0]["message"]["tool_calls"][0])
    first["id"] = "synthetic_lookup"
    first["function"] = {"name": "synthetic__lookup", "arguments": "{}"}
    value["choices"][0]["message"]["tool_calls"].insert(0, first)
    return value


def test_batch_boundary_requires_all_ordered_results():
    from recoveragent.teacher_history import validate_history

    assistant = normalize_message(batch_response(), max_calls=2)
    first = {"role": "tool", "tool_call_id": "synthetic_lookup", "content": "one"}
    second = {"role": "tool", "tool_call_id": "synthetic_call_1", "content": "two"}
    with pytest.raises(ValueError, match="unfinished"):
        validate_history([assistant, first])
    with pytest.raises(ValueError, match="Unmatched"):
        validate_history([assistant, second, first])
    validate_history([assistant, first, second])
    with pytest.raises(ValueError, match="limit"):
        normalize_message(batch_response(), max_calls=1)


def test_batch_is_validated_before_any_dispatch(tmp_path, monkeypatch, config):
    # The first call is valid, but the second is not visible: execute neither.
    value = batch_response()
    value["choices"][0]["message"]["tool_calls"].reverse()
    _, result = collect_fixture(tmp_path, monkeypatch, config, provider_response=value)
    assert result["usage"]["tool_calls"] == 0
    assert result["termination_reason"] == "invalid_completion"


def test_batch_budget_does_not_execute_prefix(tmp_path, monkeypatch, config):
    config["limits"]["max_tool_calls"] = 1
    _, result = collect_fixture(
        tmp_path, monkeypatch, config, provider_response=batch_response()
    )
    assert result["usage"]["tool_calls"] == 0
    assert "budget" in result["turns"][0]["format_error"]


def test_batch_preserved_as_one_assistant_and_multiple_results(
    tmp_path, monkeypatch, config
):
    batch_tools = [
        {
            "type": "function",
            "function": {
                "name": "synthetic__lookup",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        *TOOLS,
    ]
    dispatched = []

    class BatchWorld(Adapter):
        versions = {"revision": APPWORLD_REVISION, "data_version": "SYNTHETIC"}

        def initial_tools(self):
            return batch_tools

        def execute(self, name, arguments, tools):
            dispatched.append(name)
            self.completed = name == "supervisor__complete_task"
            return name + " result", tools

    monkeypatch.setattr(p3a, "AppWorldAdapter", BatchWorld)
    monkeypatch.setattr(
        p3a,
        "TeacherPolicy",
        lambda cfg, creds: TeacherPolicy(
            cfg, creds, transport=lambda p: batch_response()
        ),
    )
    monkeypatch.setattr(
        p3a, "official_evaluate", lambda *a: (True, {"synthetic": True})
    )
    run = tmp_path / "batch_run"
    write_json(run / "config.json", config)
    result = p3a.run_teacher_episode(
        config, "unit_task", run / "teacher_batch", {"DEEPSEEK_API_KEY": "unit-secret"}
    )
    assert result["status"] == "valid" and result["usage"]["tool_calls"] == 2
    assert dispatched == ["synthetic__lookup", "supervisor__complete_task"]
    assert [m["role"] for m in result["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    manifest = export_dataset(
        run, tmp_path / "batch_data", {"unit_task"}, config["limits"]
    )
    assert manifest["counts"]["accepted"] == 1
    assert (
        verify_dataset(tmp_path / "batch_data", {"unit_task"})["costs"]["tool_calls"]
        == 2
    )


def test_interrupted_worker_keeps_known_receipts_and_unknown_reservation(
    tmp_path, config
):
    directory = tmp_path / "interrupted"
    for number in ("001", "002"):
        write_json(
            directory / "requests" / f"{number}.reservation.json",
            {"max_cost": 1.0, "max_tokens": 512},
        )
    write_json(directory / "requests" / "001.response.json", response())
    write_json(directory / "dispatches" / "001_001.json", {"content": "synthetic"})
    usage = p3a.interrupted_usage(directory, config["teacher"])
    assert usage["provider_requests"] == 2
    assert usage["generated_tokens"] == 20 and usage["tool_calls"] == 1
    assert usage["usage_priced_cost"] == pytest.approx(0.000095)
    assert usage["unknown_cost_reservation"] == 1.0
    assert usage["unknown_generation_reservation"] == 512 and not usage["usage_known"]
