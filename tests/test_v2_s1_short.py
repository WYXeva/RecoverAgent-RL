"""Short-update software contracts; synthetic data/transport, no paid calls."""

import copy
import gzip
import json
from pathlib import Path

import pytest

from recoveragent import v2_short_data as data
from recoveragent import v2_s1_short as entry
from recoveragent.io import read_json
from recoveragent.demonstrations import sha256
from recoveragent.teacher_answer_format import PROTOCOL, NOTICE, public_task
from recoveragent.v2_tasks import plan
from recoveragent.v2_training import training_schedule


def test_frozen_320_plan_covers_all_pools_and_exact_token_objectives():
    cfg = entry.configuration()
    frozen = read_json(cfg["training_plan"])
    assert sha256(cfg["training_plan"]) == cfg["training_plan_sha256"]
    assert cfg["data_pools"]["old_replay"]["tasks"] == 256
    assert len(frozen["rows"]) == 320
    assert {k: v["tasks"] for k, v in frozen["groups"].items()} == {
        "old_replay": 160,
        "fresh_full": 21,
        "recovery_suffix": 18,
    }
    for source, expected in cfg["presentations"].items():
        rows = [r for r in frozen["rows"] if r["source"] == source]
        assert len(rows) == expected
        assert len({r["source_row"] for r in rows}) == expected
        tokens = sum(r["supervised_tokens"] for r in rows)
        alpha = cfg["recovery_sft"]["source_objectives"][source]
        # Arbitrary different per-row means must equal the source token mean.
        means = [0.1 + i / 7 for i in range(len(rows))]
        actual = sum(r["example_weight"] * loss for r, loss in zip(rows, means)) / 320
        expected_loss = (
            alpha
            * sum(r["supervised_tokens"] * loss for r, loss in zip(rows, means))
            / tokens
        )
        assert actual == pytest.approx(expected_loss)
        assert sum(r["example_weight"] for r in rows) / 320 == pytest.approx(alpha)
    assert training_schedule(cfg["recovery_sft"], "formal") == {
        "num_train_epochs": 1,
        "max_steps": 40,
        "per_device_train_batch_size": 1,
        "gradient_accumulation_steps": 8,
    }
    for profile, steps in [("short", 2), ("long", 1)]:
        effective = training_schedule(cfg["recovery_sft"], profile)
        assert (
            effective["max_steps"] == steps
            and effective["gradient_accumulation_steps"] == 1
        )


def test_balanced_selection_reproducible_and_covers_scenes_before_repeat_tasks():
    rows = [
        {"task_id": f"{s}_{t}", "scenario_id": str(s), "source_row": i}
        for i, (s, t) in enumerate(
            (s, t) for s in range(4) for t in range(3) for _ in range(5)
        )
    ]
    a = data.balanced_select(rows, 12, 42)
    assert a == data.balanced_select(rows, 12, 42)
    assert len({r["scenario_id"] for r in a[:4]}) == 4
    assert len({r["task_id"] for r in a}) == 12


def test_materialization_preserves_every_historical_token_and_metadata(tmp_path):
    task = next(r["task_id"] for r in plan()["rows"] if r["role"] == "train")
    row = {
        "task_id": task,
        "trajectory_id": "accepted",
        "assistant_index": 19,
        "input_ids": [3, 4, 5],
        "labels": [-100, -100, 5],
        "loss_mask": [0, 0, 1],
        "target_start": 2,
        "history_hash": "old_exact_history",
        "tools_hash": "old_exact_tools",
        "answer_format_contract": {"historical": "unchanged"},
    }
    p = tmp_path / "accepted.gz"
    with gzip.open(p, "wt") as f:
        f.write(json.dumps(row) + "\n")
    expected = {
        "file": p.name,
        "sha256": sha256(p),
        "examples": 1,
        "tasks": 1,
        "supervised_tokens": 1,
    }
    entries = data.inspect_pool(p, expected)
    weighted, _ = data.weight_plan({"recovery_suffix": entries}, {"recovery_suffix": 1})
    result = data.materialize(
        tmp_path,
        {"data_pools": {"recovery_suffix": expected}},
        {"rows": weighted, "examples": 1},
    )
    assert {k: result[0][k] for k in row} == row
    row["labels"][0] = 3
    with gzip.open(p, "wt") as f:
        f.write(json.dumps(row) + "\n")
    expected["sha256"] = sha256(p)
    with pytest.raises(ValueError, match="mask"):
        data.inspect_pool(p, expected)


@pytest.mark.parametrize(
    "instruction",
    [
        "Give one amount.",
        "Return identifier 001-02.",
        "Return 30%.",
        "Format the answer as $1,234.00.",
        "Return two names.",
    ],
)
def test_format_preserves_explicit_requirements_and_is_idempotent(instruction):
    source = {"instruction": instruction}
    before = copy.deepcopy(source)
    formatted = public_task(source, {"answer_format_contract": PROTOCOL})
    assert formatted["instruction"] == instruction and source == before
    assert formatted == public_task(formatted, {"answer_format_contract": PROTOCOL})


@pytest.mark.parametrize("solver", [False, True])
def test_teacher_and_generated_solver_receive_shared_notice(
    tmp_path, monkeypatch, solver
):
    from test_teacher_data import config, install_fixture
    from recoveragent import p3a, v2_generator

    cfg = config.__wrapped__()
    cfg["teacher"]["answer_format_contract"] = PROTOCOL
    install_fixture(monkeypatch, cfg)
    exploration = None
    if solver:
        cfg["v2_runtime"] = entry.RUNTIME
        monkeypatch.setattr(p3a, "validate_config", lambda *_: None)
        exploration = {
            "mode": "validation",
            "task": {"instruction": "Return generated count."},
        }
        monkeypatch.setattr(
            v2_generator,
            "exploration_instruction",
            lambda payload: payload["task"]["instruction"],
        )

        def apply(adapter, instruction):
            original = adapter.public_task()
            adapter.public_task = lambda: {**original, "instruction": instruction}

        monkeypatch.setattr(v2_generator, "apply_instruction", apply)
    result = p3a.run_teacher_episode(
        cfg,
        "synthetic",
        tmp_path / "teacher",
        {"DEEPSEEK_API_KEY": "synthetic"},
        exploration=exploration,
    )
    visible = json.loads(result["messages"][1]["content"])
    assert visible["answer_format_contract"] == NOTICE
    if solver:
        assert visible["instruction"] == "Return generated count."


def test_new_teacher_and_student_protocol_same_and_historical_configuration_unchanged():
    from recoveragent.v2_teacher import configuration

    cfg = entry.configuration()
    task = next(r["task_id"] for r in plan()["rows"] if r["role"] == "train")
    teacher = configuration(
        task,
        cfg["models"]["primary"],
        Path("synthetic"),
        1,
        recovery=False,
        runtime=entry.RUNTIME,
    )
    assert teacher["teacher"]["answer_format_contract"] == cfg["answer_format_contract"]
    assert "answer_format_contract" not in read_json(
        "configs/v2_s1_retrain_20261114.json"
    )


def test_both_checkpoints_dispatch_identical_evaluation_contract(tmp_path, monkeypatch):
    import sys
    import time
    from types import SimpleNamespace
    from recoveragent import v2_s1_batch as batch, v2_model, v2_inference

    cfg = entry.configuration()
    cfg["dev_task_ids"] = cfg["dev_task_ids"][:2]
    model = SimpleNamespace(eval=lambda: None)
    monkeypatch.setitem(
        sys.modules,
        "peft",
        SimpleNamespace(
            PeftModel=SimpleNamespace(from_pretrained=lambda *a, **kw: model)
        ),
    )
    monkeypatch.setattr(v2_model, "load_base", lambda *_: model)
    monkeypatch.setattr(v2_model, "load_tokenizer", lambda *_: object())
    monkeypatch.setattr(
        batch, "checkpoint", lambda path, *a, **kw: {"adapter_sha256_after": path}
    )
    seen = []

    def episode(request, *args, **kwargs):
        seen.append(copy.deepcopy(request))
        assert kwargs["generate_fn"] is None
        return {"classification": "natural_failure", "task_id": request["task_id"]}

    monkeypatch.setattr(v2_inference, "episode", episode)
    deploy = {
        "s0": "accepted_S0",
        "s1": "short_S1",
        "appworld_root": "same_world",
        "worker_python": "synthetic",
        "job_deadline_epoch": time.time() + 99999,
    }
    for key in ("s0", "s1"):
        batch.feedback(cfg, deploy, tmp_path, evaluation=True, evaluation_model=key)
    assert len(seen) == 4
    for a, b in zip(seen[:2], seen[2:]):
        assert a["checkpoint_id"] != b["checkpoint_id"]
        assert {k: v for k, v in a.items() if k not in {"checkpoint_id", "output"}} == {
            k: v for k, v in b.items() if k not in {"checkpoint_id", "output"}
        }
        assert a["answer_format_contract"] == PROTOCOL


def test_formal_entry_drives_40_updates_and_reload_of_step20(tmp_path, monkeypatch):
    """Synthetic Trainer proves argument/callback wiring; real GPU test is separate."""
    import sys
    from types import SimpleNamespace as NS
    from recoveragent import v2_training as training
    from recoveragent.v2_data import write_rows

    cfg = entry.configuration()
    observed = []
    optimizer = NS(state_dict=lambda: {"synthetic": True})
    model = NS(train=lambda: None)

    class Trainer:
        def __init__(self, **kwargs):
            self.args, self.callbacks = kwargs["args"], kwargs["callbacks"]
            self.dataset = kwargs["train_dataset"]
            self.state, self.optimizer = NS(global_step=0), optimizer
            observed.append(vars(self.args))

        def get_train_dataloader(self):
            return self.dataset

        def train(self):
            for step in range(self.args.max_steps):
                for cb in self.callbacks:
                    cb.on_step_begin(self.args, self.state, None)
                    cb.on_pre_optimizer_step(self.args, self.state, None, model=model)
                self.state.global_step = step + 1
                for cb in self.callbacks:
                    cb.on_step_end(
                        self.args, self.state, None, model=model, optimizer=optimizer
                    )

        def save_state(self):
            pass

    cuda = NS(
        is_available=lambda: True,
        is_bf16_supported=lambda: True,
        device_count=lambda: 1,
        reset_peak_memory_stats=lambda: None,
        max_memory_allocated=lambda: 0,
        max_memory_reserved=lambda: 0,
        empty_cache=lambda: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        NS(cuda=cuda, save=lambda obj, path: Path(path).write_text("synthetic")),
    )
    monkeypatch.setitem(sys.modules, "peft", NS(get_peft_model_state_dict=lambda _: {}))
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        NS(
            Trainer=Trainer,
            TrainerCallback=object,
            TrainingArguments=NS,
            set_seed=lambda _: None,
        ),
    )
    monkeypatch.setattr(training, "load_trainable", lambda *a: model)
    monkeypatch.setattr(training, "load_tokenizer", lambda *a: object())
    monkeypatch.setattr(training, "tensor_hash", lambda _: "S0")
    monkeypatch.setattr(training, "gradient_health", lambda _: {"finite": True})
    monkeypatch.setattr(
        training, "finish_checkpoint", lambda *a, **kw: ("updated", "synthetic_probe")
    )
    reloads = []

    def reload(*args):
        reloads.append((str(args[0]), args[-1]))
        return args[-1]

    monkeypatch.setattr(training, "finalize_reload", reload)
    task = next(r["task_id"] for r in plan()["rows"] if r["role"] == "train")
    rows = [
        {
            "task_id": task,
            "input_ids": [3, 4, 5],
            "target_start": 2,
            "labels": [-100, -100, 5],
            "loss_mask": [0, 0, 1],
        }
        for _ in range(320)
    ]
    data_file = tmp_path / "rows.jsonl"
    write_rows(data_file, rows)
    receipt = training.train_sft(
        cfg,
        cfg["models"]["primary"],
        data_file,
        tmp_path / "formal",
        parent="accepted_S0",
    )
    assert receipt["optimizer_steps"] == 40
    assert [r[1]["optimizer_steps"] for r in reloads] == [20, 40]
    assert observed[0]["max_steps"] == 40
    assert observed[0]["gradient_accumulation_steps"] == 8
    assert observed[0]["learning_rate"] == 5e-6
