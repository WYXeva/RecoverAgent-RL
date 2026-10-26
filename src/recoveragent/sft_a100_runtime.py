"""Frozen A100 hardware variant of the verified BF16 training runtime.

Training/loss/update/reload operations are retained verbatim. Only the hardware
preflight, supplement lock, and recorded hardware contract differ. Original
BF16 and dataset source locks remain unchanged and continue to be checked.
"""

import argparse
import hashlib
import math
from pathlib import Path

from .base_sft import MODEL, REVISION
from .formal_dataset import json_lines, verify, verify_runtime_protocol
from .io import files_manifest, read_json, write_json
from .sft_recipe import (
    preflight as original_preflight,
    collate,
    tensor_hash,
    gradient_health,
    selected_token_loss,
    validate_config,
)


def verify_supplement(repository="."):
    root = Path(repository)
    lock = read_json(root / "configs/p3b_a100_runtime_20261026_lock.json")
    if lock["runtime_contract"] != "bf16_autocast_a100_20261026":
        raise ValueError("Unknown BF16 runtime supplement")
    for name, expected in lock["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Invalid runtime supplement path")
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("BF16 runtime source mismatch: " + name)
    return lock


def preflight(config):
    check = original_preflight(config)
    import torch

    suitable = False
    if torch.cuda.is_available() and torch.cuda.device_count() == 1:
        props = torch.cuda.get_device_properties(0)
        suitable = (
            "A100" in props.name
            and props.total_memory >= 75 * 1024**3
            and torch.cuda.is_bf16_supported()
        )
    # Replace the one hardware-specific legacy gate only after checking the
    # explicitly authorized A100 profile. All dependency/BF16 issues remain.
    issues = list(check["issues"])
    if suitable:
        issues = [i for i in issues if i != "REMOTE_96GB_GPU_NOT_VERIFIED"]
    else:
        issues.append("A100_80GB_NOT_VERIFIED")
    return {
        **check,
        "status": "BLOCKED" if issues else "READY_FOR_EXPLICIT_SMOKE",
        "issues": issues,
        "hardware_contract": "a100_80gb_20261026",
    }


def execute(config, dataset, output):
    verify_supplement()
    check = preflight(config)
    if check["issues"]:
        raise ValueError("Remote preflight has unresolved requirements")
    verify(dataset, config["dataset_manifest_sha256"])
    verify_runtime_protocol(dataset)
    output = Path(output)
    if output.exists():
        raise FileExistsError("Use a new checkpoint output directory")
    write_json(output / "run_config.json", config)
    import torch
    from transformers import (
        AutoModelForCausalLM,
        Trainer,
        TrainerCallback,
        TrainingArguments,
        set_seed,
    )
    from peft import LoraConfig, get_peft_model, get_peft_model_state_dict, PeftModel
    from .base_sft import load_tokenizer

    tokenizer = load_tokenizer()
    train = list(json_lines(Path(dataset) / "tokenized/train.jsonl"))
    validation = list(json_lines(Path(dataset) / "tokenized/validation.jsonl"))
    if config["profile"] == "smoke":
        # Select existing complete examples; never clip a history or its target.
        def choose(rows):
            eligible = [
                r
                for r in rows
                if len(r["input_ids"]) <= config["smoke_max_example_length"]
            ]
            return sorted(
                eligible,
                key=lambda r: len(r["input_ids"]),
                reverse=config["smoke_selection"] == "longest",
            )[: config["smoke_examples"]]

        train, validation = choose(train), choose(validation)
        if not train or not validation:
            raise ValueError("No complete short smoke examples")
    write_json(
        output / "example_selection.json",
        {
            name: [
                {
                    "trajectory_id": r["trajectory_id"],
                    "assistant_index": r["assistant_index"],
                    "sequence_tokens": len(r["input_ids"]),
                }
                for r in rows
            ]
            for name, rows in [("train", train), ("validation", validation)]
        },
    )
    set_seed(config["training_args"]["seed"])
    model = AutoModelForCausalLM.from_pretrained(
        MODEL,
        revision=REVISION,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    model = get_peft_model(model, LoraConfig(**config["lora"]))
    if any(
        p.requires_grad and "lora_" not in name for name, p in model.named_parameters()
    ):
        raise ValueError("Base weights must remain frozen")
    before = tensor_hash(get_peft_model_state_dict(model))

    class WeightedTrainer(Trainer):
        def compute_loss(
            self, model, inputs, return_outputs=False, num_items_in_batch=None
        ):
            loss, outputs = selected_token_loss(model, inputs)
            return (loss, outputs) if return_outputs else loss

    gradient_checks = []

    class GradientCheck(TrainerCallback):
        def on_pre_optimizer_step(self, args, state, control, model=None, **kwargs):
            gradient_checks.append(
                {"step": state.global_step + 1, **gradient_health(model)}
            )

    args = TrainingArguments(output_dir=str(output), **config["training_args"])
    trainer = WeightedTrainer(
        model=model,
        args=args,
        train_dataset=train,
        eval_dataset=validation,
        data_collator=collate,
        processing_class=tokenizer,
        callbacks=[GradientCheck()],
    )
    # Use the pinned Trainer microbatch averaging path; no token-count rescaling.
    trainer.model_accepts_loss_kwargs = False
    torch.cuda.reset_peak_memory_stats()
    training = trainer.train()
    after = tensor_hash(get_peft_model_state_dict(model))
    if (
        not gradient_checks
        or before == after
        or not math.isfinite(training.metrics["train_loss"])
    ):
        raise ValueError("No finite, real LoRA update demonstrated")
    evaluation = trainer.evaluate()
    if not math.isfinite(evaluation["eval_loss"]):
        raise ValueError("Nonfinite validation loss")
    trainer.save_model()
    tokenizer.save_pretrained(output)
    probe = collate([train[0]])
    probe = {
        k: v.to(model.device)
        for k, v in probe.items()
        if k in ["input_ids", "attention_mask"]
    }
    model.eval()
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        expected = (
            model(**probe, logits_to_keep=1, use_cache=False).logits.float().cpu()
        )
    peak = torch.cuda.max_memory_allocated()
    del trainer, model
    import gc

    gc.collect()
    torch.cuda.empty_cache()
    base = AutoModelForCausalLM.from_pretrained(
        MODEL,
        revision=REVISION,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    restored = (
        PeftModel.from_pretrained(base, str(output), is_trainable=False)
        .to("cuda")
        .eval()
    )
    restored_hash = tensor_hash(get_peft_model_state_dict(restored))
    if restored_hash != after:
        raise ValueError("Restored LoRA tensors differ from saved parameters")
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        actual = (
            restored(**probe, logits_to_keep=1, use_cache=False).logits.float().cpu()
        )
    if not torch.allclose(expected, actual, atol=0.01, rtol=0.01):
        raise ValueError("Checkpoint reload numerical mismatch")
    result = {
        "status": "PASS",
        "runtime_contract": "bf16_autocast_reload_v1",
        "hardware_preflight": check,
        "profile": config["profile"],
        "model": MODEL,
        "revision": REVISION,
        "dataset_manifest_sha256": config["dataset_manifest_sha256"],
        "trainable_before_hash": before,
        "trainable_after_hash": after,
        "gradient_checks": gradient_checks,
        "checkpoint_reloaded": True,
        "restored_trainable_hash": restored_hash,
        "reload_max_abs_error": float((expected - actual).abs().max()),
        "peak_allocated_bytes": peak,
        "metrics": training.metrics,
        "evaluation": evaluation,
        "training_started": True,
        "parameter_update_and_reload_pass": True,
        "p3b_complete": False,  # Also requires real Student/dev evaluation and controls.
        "files": files_manifest(output),
    }
    write_json(output / "recoveragent_checkpoint.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["validate", "preflight", "execute"])
    p.add_argument("--config", required=True)
    p.add_argument("--dataset")
    p.add_argument("--output")
    p.add_argument("--expected-manifest-sha256")
    p.add_argument("--report", required=True)
    a = p.parse_args()
    c = read_json(a.config)
    if a.expected_manifest_sha256:
        c["dataset_manifest_sha256"] = a.expected_manifest_sha256
    if a.command == "execute" and not (
        a.dataset and a.output and c.get("dataset_manifest_sha256")
    ):
        p.error(
            "Execution requires a dataset, new output, and trusted manifest SHA-256"
        )
    result = (
        validate_config(c)
        if a.command == "validate"
        else preflight(c)
        if a.command == "preflight"
        else execute(c, a.dataset, a.output)
    )
    write_json(a.report, result)
    print(result)


if __name__ == "__main__":
    main()
