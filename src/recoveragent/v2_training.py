"""BF16 LoRA stages with explicit parent lineage and save/reload verification."""

from contextlib import nullcontext
import gc
import math
import time
from pathlib import Path

from .demonstrations import sha256
from .io import files_manifest, read_json, write_json
from .sft_recipe import collate, gradient_health, selected_token_loss, tensor_hash
from .v2_data import read_rows
from .v2_model import load_base, load_tokenizer
from .v2_tasks import context_for


def verify_checkpoint(path, identity):
    path = Path(path)
    receipt = read_json(path / "checkpoint_receipt.json")
    actual = files_manifest(path)
    actual.pop("checkpoint_receipt.json")
    if (
        receipt["model"] != identity
        or actual != receipt["files"]
        or not receipt["reload_passed"]
    ):
        raise ValueError("Checkpoint identity/files/reload receipt mismatch")
    return receipt


def load_trainable(identity, lora, parent=None, *, device="cuda"):
    from peft import LoraConfig, PeftModel, get_peft_model, get_peft_model_state_dict

    model = load_base(identity, device=device)
    if parent is not None:
        receipt = verify_checkpoint(parent, identity)
        model = PeftModel.from_pretrained(model, str(parent), is_trainable=True)
        if (
            tensor_hash(get_peft_model_state_dict(model))
            != receipt["adapter_sha256_after"]
        ):
            raise ValueError("Continued adapter does not equal the saved parent")
    else:
        lora = dict(lora)
        if identity["tool_format"] == "qwen_xml":
            lora["target_modules"] = (
                r".*language_model\..*\.(" + "|".join(lora["target_modules"]) + ")"
            )
        model = get_peft_model(model, LoraConfig(**lora))
    if not any(p.requires_grad for p in model.parameters()) or any(
        p.requires_grad and "lora_" not in n for n, p in model.named_parameters()
    ):
        raise ValueError("Only LoRA parameters may be trainable")
    model.enable_input_require_grads()
    return model


def finish_checkpoint(
    model, tokenizer, output, identity, before, *, parent, metadata, probe_ids
):
    import torch
    from peft import get_peft_model_state_dict

    after = tensor_hash(get_peft_model_state_dict(model))
    if after == before or any(
        not torch.isfinite(t).all() for t in get_peft_model_state_dict(model).values()
    ):
        raise ValueError("Parameters unchanged or nonfinite")
    model.eval()
    with (
        torch.no_grad(),
        torch.autocast(
            model.device.type, dtype=torch.bfloat16, enabled=model.device.type == "cuda"
        ),
    ):
        probe = (
            model(
                input_ids=torch.tensor([probe_ids], device=model.device),
                logits_to_keep=1,
                use_cache=False,
            )
            .logits.float()
            .cpu()
        )
    model.save_pretrained(output, safe_serialization=True)
    tokenizer.save_pretrained(output)
    write_json(
        Path(output) / "reload_probe.json",
        {"input_ids": probe_ids, "logits_sha256": tensor_hash({"logits": probe})},
    )
    return after, probe


def finalize_reload(
    output, identity, before, after, expected, probe_ids, parent, metadata
):
    import torch
    from peft import PeftModel, get_peft_model_state_dict

    base = load_base(identity)
    model = PeftModel.from_pretrained(base, str(output), is_trainable=False)
    model.eval()
    with (
        torch.no_grad(),
        torch.autocast(
            model.device.type, dtype=torch.bfloat16, enabled=model.device.type == "cuda"
        ),
    ):
        actual = (
            model(
                input_ids=torch.tensor([probe_ids], device=model.device),
                logits_to_keep=1,
                use_cache=False,
            )
            .logits.float()
            .cpu()
        )
    write_json(
        Path(output) / "reload_result.json",
        {
            "adapter_sha256_expected": after,
            "adapter_sha256_actual": tensor_hash(get_peft_model_state_dict(model)),
            "precision": "bf16_autocast_on_cuda",
            "max_abs_logit_difference": float((actual - expected).abs().max()),
            "logits_exactly_equal": torch.equal(actual, expected),
        },
    )
    if tensor_hash(get_peft_model_state_dict(model)) != after or not torch.allclose(
        actual, expected, atol=1e-2, rtol=1e-2
    ):
        raise ValueError("Saved checkpoint reload differs")
    del model, base
    gc.collect()
    torch.cuda.empty_cache()
    receipt = {
        **metadata,
        "model": identity,
        "parent": str(parent) if parent else None,
        "parent_receipt_sha256": sha256(Path(parent) / "checkpoint_receipt.json")
        if parent
        else None,
        "adapter_sha256_before": before,
        "adapter_sha256_after": after,
        "reload_passed": True,
        "files": files_manifest(output),
    }
    write_json(Path(output) / "checkpoint_receipt.json", receipt)
    return receipt


def train_sft(
    config, identity, examples, output, *, parent=None, profile="formal", deadline=None
):
    import torch
    from peft import get_peft_model_state_dict
    from transformers import Trainer, TrainerCallback, TrainingArguments, set_seed

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise ValueError("BF16 CUDA GPU unavailable")
    if torch.cuda.device_count() != 1:
        raise ValueError("This run requires one visible GPU")
    rows = read_rows(examples)
    for row in rows:
        context_for(row["task_id"], training=True)
        if (
            len(row["input_ids"]) > 32768
            or row["labels"][: row["target_start"]] != [-100] * row["target_start"]
        ):
            raise ValueError("Invalid train scope, length, or loss mask")
    stage = config["recovery_sft" if parent else "base_sft"]
    steps = -1
    accumulation = stage["gradient_accumulation_steps"]
    if profile == "short":
        rows = sorted(
            (r for r in rows if len(r["input_ids"]) <= 8192),
            key=lambda r: len(r["input_ids"]),
        )[:4]
        steps, accumulation = 2, 1
    elif profile == "long":
        rows = [max(rows, key=lambda r: len(r["input_ids"]))]
        steps, accumulation = 1, 1
    elif profile != "formal":
        raise ValueError("Unknown training profile")
    if not rows:
        raise ValueError("No complete examples for selected profile")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "training_config.json",
        {
            "run": config,
            "stage": stage,
            "model": identity,
            "profile": profile,
            "parent": str(parent) if parent else None,
            "data_sha256": sha256(examples),
        },
    )
    set_seed(20261109)
    started = time.monotonic()
    torch.cuda.reset_peak_memory_stats()
    model = load_trainable(identity, config["lora"], parent)
    tokenizer = load_tokenizer(identity)
    before = tensor_hash(get_peft_model_state_dict(model))
    gradients, losses = [], []
    step_started = [None]

    class Health(TrainerCallback):
        def on_step_begin(self, args, state, control, **kwargs):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Shared GPU job deadline reached")
            step_started[0] = time.monotonic()

        def on_pre_optimizer_step(self, args, state, control, **kwargs):
            gradients.append(
                {
                    "step": state.global_step + 1,
                    "forward_backward_seconds": time.monotonic() - step_started[0],
                    **gradient_health(kwargs["model"]),
                }
            )
            write_json(
                output / "gradients" / f"{state.global_step + 1:05d}.json",
                gradients[-1],
            )

        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs:
                if any(
                    type(v) in (float, int) and not math.isfinite(v)
                    for v in logs.values()
                ):
                    raise ValueError("Nonfinite training metric")
                losses.append({"step": state.global_step, **logs})
                write_json(output / "loss_logs" / f"{len(losses):05d}.json", losses[-1])

    class SuffixTrainer(Trainer):
        def compute_loss(
            self, model, inputs, return_outputs=False, num_items_in_batch=None
        ):
            context = (
                torch.autograd.graph.save_on_cpu(pin_memory=True)
                if config.get("activation_offload") == "cpu"
                else nullcontext()
            )
            with context:
                loss, outputs = selected_token_loss(model, inputs)
            if not torch.isfinite(loss):
                raise ValueError("Nonfinite SFT loss")
            return (loss, outputs) if return_outputs else loss

    args = TrainingArguments(
        output_dir=str(output / "trainer"),
        num_train_epochs=1,
        max_steps=steps,
        learning_rate=stage["learning_rate"],
        per_device_train_batch_size=1,
        gradient_accumulation_steps=accumulation,
        bf16=True,
        fp16=False,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=1,
        save_strategy="no",
        eval_strategy="no",
        report_to=[],
        remove_unused_columns=False,
        seed=20261109,
        data_seed=20261109,
        dataloader_num_workers=0,
        optim="adamw_torch",
        max_grad_norm=1.0,
    )
    trainer = SuffixTrainer(
        model=model,
        args=args,
        train_dataset=rows,
        data_collator=collate,
        callbacks=[Health()],
    )
    expected_steps = (
        steps
        if steps > 0
        else math.ceil(len(trainer.get_train_dataloader()) / accumulation)
    )
    try:
        trainer.train()
    except BaseException as exc:
        failure = {
            "error_type": type(exc).__name__,
            "optimizer_steps": trainer.state.global_step,
            "training_completed": False,
            "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "wall_seconds": time.monotonic() - started,
        }
        try:
            model.save_pretrained(
                output / "interrupted_adapter", safe_serialization=True
            )
            failure["partial_adapter_sha256"] = tensor_hash(
                get_peft_model_state_dict(model)
            )
        except Exception as save_error:
            failure["partial_save_error_type"] = type(save_error).__name__
        write_json(output / "training_failure.json", failure)
        raise
    if trainer.state.global_step != expected_steps or len(gradients) != expected_steps:
        raise ValueError("Actual optimizer steps differ from dataloader")
    trainer.save_state()
    write_json(output / "loss_curve.json", losses)
    write_json(output / "gradients.json", gradients)
    torch.save(trainer.optimizer.state_dict(), output / "optimizer.pt")
    probe_ids = rows[0]["input_ids"][: min(128, rows[0]["target_start"])]
    metadata = {
        "stage": "S1_recovery" if parent else "S0_base",
        "profile": profile,
        "optimizer_steps": trainer.state.global_step,
        "examples": len(rows),
        "data_sha256": sha256(examples),
        "supervised_tokens": sum(sum(r["loss_mask"]) for r in rows),
        "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "training_wall_seconds": time.monotonic() - started,
    }
    after, probe = finish_checkpoint(
        model,
        tokenizer,
        output,
        identity,
        before,
        parent=parent,
        metadata=metadata,
        probe_ids=probe_ids,
    )
    del trainer, model
    gc.collect()
    torch.cuda.empty_cache()
    return finalize_reload(
        output, identity, before, after, probe, probe_ids, parent, metadata
    )
