"""New-main-line tests; synthetic fixtures are not Teacher collection evidence."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from recoveragent.codeact import client, data, episode, protocol, training
from recoveragent.codeact.collect import queue
from recoveragent.io import read_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return read_json(ROOT / "configs/codeact_reboot_20261119.json")


def response(code, index=0):
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "PRIVATE THOUGHT NEVER TRAIN",
                    "tool_calls": [
                        {
                            "id": f"call_{index}",
                            "type": "function",
                            "function": {
                                "name": "appworld",
                                "arguments": json.dumps({"code": code}),
                            },
                        }
                    ],
                },
            }
        ]
    }


def test_frozen_queue_scenario_round_robin():
    rows = queue()
    assert len(rows) == len({r["task_id"] for r in rows}) == 510
    assert len({r["scenario_id"] for r in rows[:170]}) == 170
    assert len({r["scenario_id"] for r in rows[:20]}) == 20
    assert [r["scenario_id"] for r in rows[:170]] == [
        r["scenario_id"] for r in rows[170:340]
    ]
    assert {r["role"] for r in rows} == {"train"}


def test_episode_clock_skips_lazy_model_modules(config, tmp_path, monkeypatch):
    import datetime
    import sys
    import types

    from freezegun import freeze_time

    class LazyModel(types.ModuleType):
        def __dir__(self):
            return ["MissingTorchModel"]

        def __getattr__(self, name):
            if name == "MissingTorchModel":
                raise NameError("torch is not defined")
            raise AttributeError(name)

    class ClockChecked(Exception):
        pass

    def world(**kwargs):
        assert kwargs["load_ground_truth"] is False
        with freeze_time("2023-01-02"):
            assert datetime.datetime.now() == datetime.datetime(2023, 1, 2)
        raise ClockChecked

    monkeypatch.setitem(
        sys.modules, "transformers.lazy_probe", LazyModel("transformers.lazy_probe")
    )
    monkeypatch.setitem(
        sys.modules,
        "appworld",
        SimpleNamespace(AppWorld=world, update_root=lambda _: None),
    )
    monkeypatch.setattr(episode, "verify_environment", lambda _: {})
    monkeypatch.setattr(episode, "authorize_task", lambda *args: None)
    monkeypatch.setattr(
        episode.importlib.metadata,
        "distribution",
        lambda _: SimpleNamespace(
            read_text=lambda _: json.dumps({"revision": config["appworld_revision"]})
        ),
    )
    with pytest.raises(ClockChecked):
        episode.run(
            queue()[0]["task_id"],
            1,
            tmp_path / "episode",
            tmp_path,
            config,
            SimpleNamespace(),
        )


def test_dev24_selector_and_once_only_evaluation_resume(config, tmp_path, monkeypatch):
    from recoveragent.codeact import inference
    from recoveragent.io import write_json
    from recoveragent.v2_tasks import plan

    tasks = inference.evaluation_tasks("dev", "dev24")
    assert tasks == plan()["dev24"] and len(tasks) == 24
    assert not set(tasks) & {r["task_id"] for r in queue()}
    with pytest.raises(ValueError, match="only evaluate"):
        inference.evaluation_tasks("train", "dev24")
    monkeypatch.setattr(inference, "verify_checkpoint", lambda *args: {"sha": "new_s0"})
    monkeypatch.setattr(
        inference, "Student", lambda *args: SimpleNamespace(reset_usage=lambda: None)
    )
    calls = []

    def run(task, attempt, target, root, config, client, *, training):
        assert not training and attempt == 1
        assert not target.exists()
        calls.append(task)
        result = {
            "task_id": task,
            "success": [True, False, None][len(calls) % 3],
            "termination": "task_completed",
            "seconds": 1.0,
            "usage": {"requests": 2},
        }
        write_json(target / "result.json", result)
        return result

    monkeypatch.setattr(inference, "run", run)
    for _ in range(2):
        inference.evaluate(
            config,
            tmp_path / "eval",
            "unused",
            tmp_path / "adapter",
            "dev",
            task_set="dev24",
        )
    assert calls == tasks
    result = read_json(tmp_path / "eval/summary.json")
    assert result["status"] == "complete" and result["tasks"] == 24
    assert result["success"] == result["failure"] == result["unknown"] == 8
    assert result["usage"]["requests"] == 48
    monkeypatch.setattr(
        inference, "verify_checkpoint", lambda *args: {"sha": "different_s0"}
    )
    with pytest.raises(ValueError, match="binding mismatch"):
        inference.evaluate(
            config,
            tmp_path / "eval",
            "unused",
            tmp_path / "adapter",
            "dev",
            task_set="dev24",
        )


def test_codeact_vllm_real_template_and_shared_execution_loop(
    config, tokenizer, tmp_path, monkeypatch
):
    import requests
    from recoveragent.codeact import inference
    from recoveragent.io import digest, write_json

    checkpoint = {"protocol": protocol.identity(), "formal_s0": True}
    proof = {
        "protocol": protocol.identity(),
        "base": config["model"],
        "adapter_receipt_sha256": digest(checkpoint),
        "max_model_len": 65536,
        "dtype": "bfloat16",
        "vllm_version": "0.19.0",
        "url": "http://127.0.0.1:18019",
        "model_name": "new_codeact_s0",
    }
    write_json(tmp_path / "server.json", proof)
    monkeypatch.setattr(inference, "verify_checkpoint", lambda *args: checkpoint)
    monkeypatch.setattr(inference, "load_tokenizer", lambda *args: tokenizer)

    class World:
        task = SimpleNamespace(
            instruction="Synthetic public task", supervisor={}, datetime="2020-01-01"
        )
        done = False

        def execute(self, code):
            assert code == "x = 7\nprint(x)"
            self.done = True
            return "7"

        def task_completed(self):
            return self.done

    world = World()
    history = protocol.public_messages(world)
    from recoveragent.teacher import normalize_message

    message = normalize_message(response("x = 7\nprint(x)"), allow_reasoning=True)
    kw = dict(
        tools=protocol.TOOLS, tokenize=True, return_dict=False, enable_thinking=False
    )
    prompt = tokenizer.apply_chat_template(history, add_generation_prompt=True, **kw)
    full = tokenizer.apply_chat_template(
        history + [message], add_generation_prompt=False, **kw
    )
    generated = full[len(prompt) :]
    sent = []

    def post(self, url, *, json, timeout):
        assert json["prompt"] == prompt and json["model"] == proof["model_name"]
        assert json["temperature"] == 0 and json["max_tokens"] == 4096
        assert 0 < timeout <= config["limits"]["wall_seconds"]
        sent.append(json)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "model": proof["model_name"],
                "choices": [{"token_ids": generated, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": len(prompt),
                    "completion_tokens": len(generated),
                },
            },
        )

    monkeypatch.setattr(requests.Session, "post", post)
    client = inference.VLLMStudent(
        config, tmp_path / "adapter", proof["url"], tmp_path / "server.json"
    )
    result = episode.loop(world, client, config, tmp_path / "episode")
    assert len(sent) == 1 and result["termination"] == "task_completed"
    assert not result["fault"] and not result["format_rejections"]
    assert result["usage"]["generated_tokens"] == len(generated)
    assert (
        read_json(tmp_path / "episode/requests/000.tokens.json")["generated_ids"]
        == generated
    )
    assert (
        read_json(tmp_path / "episode/requests/000.request.json")["tools"]
        == protocol.TOOLS
    )
    checkpoint["formal_s0"] = False
    with pytest.raises(ValueError, match="identity mismatch"):
        inference.VLLMStudent(
            config, tmp_path / "adapter", proof["url"], tmp_path / "server.json"
        )


def test_loop_fixed_tools_persistent_environment_and_no_hidden_labels(tmp_path, config):
    class World:
        task = SimpleNamespace(
            instruction="Synthetic task", supervisor={}, datetime="2020-01-01"
        )
        state = {}

        def execute(self, code):
            if code == "x = 7":
                self.state["x"] = 7
                return ""
            assert self.state["x"] == 7
            return "7"

        def task_completed(self):
            return getattr(self, "done", False)

    class Fake:
        usage = {"generated_tokens": 0}
        recorded = []

        def generate(self, messages, directory, index, remaining):
            self.recorded.append(copy.deepcopy(messages))
            if index:
                world.done = True
            return response("x = 7" if not index else "print(x)", index)

        def remember(self, index, result):
            pass

    world, fake = World(), Fake()
    result = episode.loop(world, fake, config, tmp_path)
    assert len(result["actions"]) == 2
    assert result["termination"] == "task_completed"
    assert "PRIVATE THOUGHT" not in json.dumps(result["messages"])
    assert json.loads(fake.recorded[0][1]["content"]) == {
        "instruction": "Synthetic task",
        "supervisor": {},
        "datetime": "2020-01-01",
    }
    assert read_json(tmp_path / "initial.json")["tools"] == protocol.TOOLS
    assert fake.recorded[1][:2] == fake.recorded[0]


@pytest.mark.parametrize("args", [{"code": 7}, {}, {"code": "print(1)", "extra": 1}])
def test_only_native_string_code(args):
    m = response("print(1)")["choices"][0]["message"]
    m["tool_calls"][0]["function"]["arguments"] = json.dumps(args)
    with pytest.raises(Exception):
        protocol.codes(m, [])


def test_teacher_real_request_construction_fixed_native_tools(
    tmp_path, config, monkeypatch
):
    requests = []
    monkeypatch.setattr(
        client, "receipt", lambda payload, settings: {"within_budget": True}
    )
    teacher = client.Teacher(config, {"DEEPSEEK_API_KEY": "synthetic-key"})

    def transport(payload):
        requests.append(copy.deepcopy(payload))
        return response("print(1)") | {
            "model": config["teacher"]["model"],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    teacher.policy.transport = transport
    teacher.generate(
        [
            {"role": "system", "content": protocol.SYSTEM},
            {"role": "user", "content": "Synthetic"},
        ],
        tmp_path,
        0,
        20,
    )
    assert requests[0]["tools"] == protocol.TOOLS
    assert requests[0]["thinking"] == {"type": "enabled"}
    assert "temperature" not in requests[0]
    assert requests[0]["max_tokens"] == 20
    assert teacher.usage["generated_tokens"] == 5


@pytest.fixture
def tokenizer(config):
    pytest.importorskip("transformers")
    from recoveragent.v2_model import load_tokenizer

    try:
        return load_tokenizer(config["model"], local_files_only=True)
    except OSError:
        pytest.skip("Prepare the pinned official tokenizer using the README command")


def synthetic_messages():
    from recoveragent.teacher import normalize_message

    return [
        {"role": "system", "content": protocol.SYSTEM},
        {"role": "user", "content": "Synthetic counting task"},
        normalize_message(response("x = 3; print(x)", 0), allow_reasoning=True),
        {"role": "tool", "tool_call_id": "call_0", "content": "UNIQUE_OBSERVATION_713"},
        normalize_message(response("print(x + 1)", 1), allow_reasoning=True),
        {"role": "tool", "tool_call_id": "call_1", "content": "4"},
    ]


def test_real_qwen_template_multiturn_masks(tokenizer):
    messages = synthetic_messages()
    original = copy.deepcopy(messages)
    rows, stats = data.encode(messages, tokenizer)
    assert messages == original
    assert len(rows) == 1
    assert rows[0]["assistant_indices"] == [2, 4]
    targets = [y for y in rows[0]["labels"] if y != -100]
    text = tokenizer.decode(targets)
    assert "x = 3" in text and "print(x + 1)" in text and "<function=appworld>" in text
    assert "UNIQUE_OBSERVATION" not in text and "PRIVATE THOUGHT" not in text
    assert len(targets) == stats["target_tokens"]
    assert stats["repeated_prefix_input_tokens"] > len(rows[0]["input_ids"])


def test_incompatible_prefix_never_forced_or_truncated(tokenizer):
    messages = synthetic_messages()
    messages.insert(4, {"role": "user", "content": "Additional public clarification"})
    rows, stats = data.encode(messages, tokenizer)
    assert (
        len(rows) == 2
    )  # Qwen removes past empty think boundaries before a newer user.
    assert sum(r["target_tokens"] for r in rows) == stats["target_tokens"]
    assert (
        rows[1]["labels"][
            : rows[1]["labels"].index(next(y for y in rows[1]["labels"] if y != -100))
        ].count(-100)
        > 0
    )


def test_hidden_reasoning_rejected(tokenizer):
    messages = synthetic_messages()
    messages[2]["reasoning_content"] = "not public"
    with pytest.raises(ValueError, match="reasoning"):
        data.encode(messages, tokenizer)


def test_student_native_template_preserves_multiline_python_string(tokenizer, config):
    from recoveragent.teacher import normalize_message
    from recoveragent.v2_model import parse_native

    code = 'items = {"n": [1, 2]}\nfor number in items["n"]:\n    print(number)'
    history = [
        {"role": "system", "content": protocol.SYSTEM},
        {"role": "user", "content": "Synthetic task"},
    ]
    assistant = normalize_message(response(code), allow_reasoning=True)
    args = {
        "tools": protocol.TOOLS,
        "enable_thinking": False,
        "tokenize": True,
        "return_dict": False,
    }
    prompt = tokenizer.apply_chat_template(history, add_generation_prompt=True, **args)
    full = tokenizer.apply_chat_template(
        history + [assistant], add_generation_prompt=False, **args
    )
    parsed = parse_native(
        tokenizer.decode(full[len(prompt) :]),
        "student",
        "stop",
        protocol.TOOLS,
        config["model"],
    )
    _, executed_code = protocol.codes(parsed, history)
    assert executed_code == code


def test_chunked_sequence_ce_and_gradients_equal_reference():
    torch = pytest.importorskip("torch")
    from torch import nn
    from recoveragent.sft_recipe import collate, selected_token_loss

    class Body(nn.Module):
        def __init__(self):
            super().__init__()
            self.embed = nn.Embedding(32, 8)

        def forward(self, input_ids, **kwargs):
            return SimpleNamespace(
                last_hidden_state=self.embed(input_ids).cumsum(dim=1)
            )

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.model, self.lm_head = Body(), nn.Linear(8, 32)

        def forward(self, logits_to_keep, **kwargs):
            return SimpleNamespace(
                logits=self.lm_head(
                    self.model(**kwargs).last_hidden_state[:, logits_to_keep]
                )
            )

    torch.manual_seed(5)
    model = Model().double()
    inputs = collate(
        [
            {
                "input_ids": [1, 2, 3, 4, 5, 6, 7],
                "attention_mask": [1] * 7,
                "labels": [-100, -100, 3, 4, -100, 6, 7],
                "example_weight": 0.7,
            }
        ]
    )
    baseline, _ = selected_token_loss(model, inputs)
    baseline.backward()
    gradients = [p.grad.clone() for p in model.parameters()]
    model.zero_grad()
    merged = training.sequence_loss(model, inputs, chunk_size=2)
    merged.backward()
    assert float(merged.detach()) == pytest.approx(float(baseline.detach()), abs=1e-6)
    for parameter, expected in zip(model.parameters(), gradients, strict=True):
        torch.testing.assert_close(parameter.grad, expected, atol=1e-6, rtol=1e-6)
    assert sum(float(g.norm()) for g in gradients) > 0


def test_new_s0_rejects_parent_before_gpu_or_dataset(config, tmp_path):
    pytest.importorskip("torch")
    config["training"]["parent_adapter"] = "/old/model"
    with pytest.raises(ValueError, match="official base"):
        training.train(config, tmp_path / "missing", tmp_path / "out")


@pytest.mark.parametrize("fail_during_backward", [False, True])
def test_oom_discards_partial_gradients_and_replays_survivors(
    monkeypatch, fail_during_backward
):
    torch = pytest.importorskip("torch")
    model = torch.nn.Linear(1, 1, bias=False).double()
    model.weight.data.fill_(0.5)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
    rows = [
        {"task_id": str(i), "input_ids": [1, 2], "target_tokens": 1, "target": float(i)}
        for i in range(3)
    ]
    calls, events = [], []

    def backward(model, row, stage, denominator):
        calls.append(row["task_id"])
        loss = (model.weight - row["target"]).square().sum() / denominator
        if row["task_id"] != "1" or fail_during_backward:
            loss.backward()
        if row["task_id"] == "1":
            raise torch.cuda.OutOfMemoryError("synthetic OOM, not a GPU test")
        return float(loss.detach())

    monkeypatch.setattr(training, "_backward_example", backward)
    active, skipped, loss = training.accumulate_batch(
        model, optimizer, rows, [0, 1, 2], {"skip_oom": True}, events.append
    )
    expected = sum((0.5 - rows[i]["target"]) ** 2 for i in [0, 2]) / 2
    expected_grad = sum(2 * (0.5 - rows[i]["target"]) for i in [0, 2]) / 2
    assert active == [0, 2] and skipped == [1]
    assert calls == ["0", "1", "0", "2"]
    assert loss == pytest.approx(expected)
    assert model.weight.grad.item() == pytest.approx(expected_grad)
    optimizer.step()
    assert model.weight.item() == pytest.approx(0.5 - 0.1 * expected_grad)
    assert events[0]["task_id"] == "1" and not events[0]["truncated"]
    assert events[0]["discarded_partial_batch_gradients"]


def test_all_oom_has_no_update_and_nonfinite_is_not_swallowed(monkeypatch):
    torch = pytest.importorskip("torch")
    model = torch.nn.Linear(1, 1)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
    rows = [{"task_id": "a", "input_ids": [1, 2], "target_tokens": 1}]

    def oom(*args):
        model.weight.sum().backward()
        raise torch.cuda.OutOfMemoryError("synthetic")

    monkeypatch.setattr(training, "_backward_example", oom)
    active, skipped, loss = training.accumulate_batch(
        model, optimizer, rows, [0], {"skip_oom": True}, lambda e: None
    )
    assert active == [] and skipped == [0] and loss == 0
    assert model.weight.grad is None
    with pytest.raises(torch.cuda.OutOfMemoryError):
        training.accumulate_batch(model, optimizer, rows, [0], {}, lambda e: None)

    def nonfinite(*args):
        raise ValueError("Nonfinite SFT loss")

    monkeypatch.setattr(training, "_backward_example", nonfinite)
    with pytest.raises(ValueError, match="Nonfinite"):
        training.accumulate_batch(
            model, optimizer, rows, [0], {"skip_oom": True}, lambda e: None
        )


def test_smoke_oom_selection_preserves_full_samples_and_fresh_formal(
    monkeypatch, config, tmp_path
):
    torch = pytest.importorskip("torch")
    rows = [{"task_id": str(i), "input_ids": [1] * (i + 2)} for i in range(4)]
    monkeypatch.setattr(training, "load_dataset", lambda *args: (rows, {}))
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", lambda: True)
    stages = []

    def fake_pass(config, selected, manifest, output, profile):
        stages.append((profile, selected))
        assert config["training"]["parent_adapter"] is None
        assert not config["training"]["resume_optimizer"]
        return {
            "optimizer_steps": 1,
            "skipped_samples": [{"task_id": "3"}] if profile == "long" else [],
        }

    monkeypatch.setattr(training, "_pass", fake_pass)
    training.train(config, "unused", tmp_path / "training")
    assert [r["task_id"] for r in stages[1][1]] == ["3", "2", "1", "0"]
    assert stages[2][1] == rows[:3]
    assert len(rows[3]["input_ids"]) == 5
    assert read_json(tmp_path / "training/sample_selection.json") == {
        "original_samples": 4,
        "selected_samples": 3,
        "smoke_oom_excluded_tasks": ["3"],
        "truncated": False,
    }


def test_actual_tiny_qwen_lora_body_chunk_loss_and_gradient_equivalence():
    torch = pytest.importorskip("torch")
    from peft import LoraConfig, get_peft_model
    from test_v2_training_cpu import tiny, inputs
    from recoveragent.sft_recipe import selected_token_loss

    model = get_peft_model(
        tiny(),
        LoraConfig(
            r=2, lora_alpha=4, target_modules=["q_proj", "v_proj"], lora_dropout=0.0
        ),
    )
    model.train()
    batch = inputs()
    baseline, _ = selected_token_loss(model, batch)
    baseline.backward()
    before = {
        n: p.grad.clone() for n, p in model.named_parameters() if p.grad is not None
    }
    model.zero_grad()
    merged = training.sequence_loss(model, batch, chunk_size=2)
    merged.backward()
    assert float(merged.detach()) == pytest.approx(float(baseline.detach()), abs=1e-6)
    errors = []
    for name, param in model.named_parameters():
        if name in before:
            torch.testing.assert_close(param.grad, before[name], atol=1e-6, rtol=1e-5)
            errors.append(float((param.grad - before[name]).abs().max()))
    print(
        {
            "tiny_qwen_loss_abs_error": abs(float(merged.detach() - baseline.detach())),
            "gradient_max_abs_error": max(errors),
        }
    )


def test_legacy_lock_is_not_resigned_for_new_codeact(monkeypatch):
    from recoveragent.v2_runtime import verify_lock, S2_RUNTIME

    monkeypatch.setenv(
        "RECOVERAGENT_S2_EXECUTION", "v2_s2_append_teacher_text_final_20261118"
    )
    with pytest.raises(ValueError, match="source changed: src/recoveragent/teacher.py"):
        verify_lock(S2_RUNTIME)


def test_export_only_new_successes_whole_sequences_and_deferred(
    tmp_path, config, tokenizer
):
    from recoveragent.io import digest, write_json

    collection = tmp_path / "collection"
    task = queue()[0]["task_id"]
    trajectory = {
        "task_id": task,
        "scenario_id": queue()[0]["scenario_id"],
        "protocol": protocol.identity(),
        "config_sha256": digest(config),
        "messages": synthetic_messages(),
    }
    write_json(
        collection / "binding.json",
        {"protocol": protocol.identity(), "config_sha256": digest(config)},
    )
    attempt = collection / "episodes" / task / "attempt_1"
    write_json(attempt / "trajectory.json", trajectory)
    write_json(
        attempt / "result.json",
        {
            "task_id": task,
            "attempt": 1,
            "success": True,
            "valid": True,
            "eligible": True,
            "trajectory_sha256": digest(trajectory),
        },
    )
    report = data.export(collection, tmp_path / "export", config, tokenizer=tokenizer)
    rows, manifest = data.load_dataset(tmp_path / "export", config)
    assert report["samples"] == 1 and len(rows[0]["assistant_indices"]) == 2
    assert rows[0]["example_weight"] == 1.0
    assert report == manifest and report["deferred_trajectories"] == 0
    assert read_json(attempt / "trajectory.json") == trajectory
    # Same collection identity, explicitly smaller training capacity produces no truncation.
    original = config["training"]["max_sequence_tokens"]
    config["training"]["max_sequence_tokens"] = 1
    with pytest.raises(ValueError, match="provenance"):
        data.export(collection, tmp_path / "bad", config, tokenizer=tokenizer)
    config["training"]["max_sequence_tokens"] = original

    class LongTokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return [9] * 65536 + tokenizer.apply_chat_template(messages, **kwargs)

    deferred = data.export(
        collection, tmp_path / "long", config, tokenizer=LongTokenizer()
    )
    assert deferred["deferred_trajectories"] == 1 and deferred["samples"] == 0
    assert not deferred["truncated"]


def test_collection_retries_clean_once_and_resume_skips_success(
    tmp_path, config, monkeypatch
):
    from recoveragent.codeact import collect as module
    from recoveragent.io import write_json

    rows = queue()[:2]
    monkeypatch.setattr(module, "queue", lambda: rows)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    audits = []
    monkeypatch.setattr(
        module,
        "audit_first_batch",
        lambda *args: audits.append(True) or {"passed": True},
    )
    config["collection"].update(max_tasks=2, first_batch_tasks=2)
    calls = []

    def worker(command, **kwargs):
        task = command[command.index("--task") + 1]
        attempt = int(command[command.index("--attempt") + 1])
        target = Path(command[command.index("--output") + 1])
        assert not target.exists()
        calls.append((task, attempt))
        success = attempt == 2 or task == rows[1]["task_id"]
        write_json(
            target / "result.json",
            {
                "task_id": task,
                "attempt": attempt,
                "valid": True,
                "success": success,
                "eligible": success,
                "usage": {},
            },
        )

    monkeypatch.setattr(module.subprocess, "run", worker)
    write_json(tmp_path / "config.json", config)  # Launcher stages this before binding.
    module.collect(config, tmp_path, "/unused", "/unused")
    module.collect(config, tmp_path, "/unused", "/unused")
    assert calls == [
        (rows[0]["task_id"], 1),
        (rows[0]["task_id"], 2),
        (rows[1]["task_id"], 1),
    ]
    assert len(audits) == 1
    assert module.summary(tmp_path)["successful_tasks"] == 2


def test_detached_launcher_preserves_venv_python_path(tmp_path, monkeypatch, config):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "codeact_launcher", ROOT / "scripts/run_codeact_collection.py"
    )
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    project = tmp_path / "repo"
    (project / "src").mkdir(parents=True)
    (project / "configs").mkdir()
    configuration = project / "configs/run.json"
    configuration.write_text(json.dumps(config))
    (project / ".env").write_text("DO_NOT_COPY")
    venv_python = tmp_path / "venv/bin/python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(sys.executable)
    monkeypatch.setattr(launcher, "__file__", str(project / "scripts/launcher.py"))
    command = []

    def spawn(args, **kwargs):
        command.extend(args)
        return SimpleNamespace(pid=987654321)

    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "launcher",
            "--python",
            str(venv_python),
            "--root",
            str(tmp_path),
            "--credentials",
            str(project / ".env"),
            "--output",
            str(tmp_path / "out"),
            "--config",
            str(configuration),
        ],
    )
    launcher.main()
    assert command[0] == str(
        venv_python
    )  # Resolving this symlink loses the venv packages.
    assert not (tmp_path / "out/runtime/.env").exists()
    original = (tmp_path / "out/runtime_manifest.json").read_bytes()
    (project / "src/new_collector.py").write_text("# New scheduling only\n")
    monkeypatch.setattr(
        sys,
        "argv",
        sys.argv
        + [
            "--runtime-name",
            "runtime_target256",
            "--target-successes",
            "256",
            "--skip-scoring-errors",
        ],
    )
    command.clear()
    launcher.main()
    assert (tmp_path / "out/runtime_manifest.json").read_bytes() == original
    assert not (tmp_path / "out/runtime/src/new_collector.py").exists()
    assert (tmp_path / "out/runtime_target256/src/new_collector.py").exists()
    assert command[-3:] == ["--target-successes", "256", "--skip-scoring-errors"]
