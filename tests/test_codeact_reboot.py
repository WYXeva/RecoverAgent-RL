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
