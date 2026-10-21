"""Future remote BF16 LoRA entry. Validation is default; execution is never implicit."""

import argparse
import hashlib
import importlib.metadata
import math
from pathlib import Path

from .base_sft import MODEL, REVISION, TEMPLATE_SHA256
from .formal_dataset import json_lines, verify, verify_runtime_protocol
from .io import files_manifest, read_json, write_json


def validate_config(config):
    if (
        config["model"] != MODEL
        or config["revision"] != REVISION
        or config["chat_template_sha256"] != TEMPLATE_SHA256
        or config["thinking"] is not False
    ):
        raise ValueError("Pinned non-thinking Qwen3-4B required")
    if config["max_sequence_tokens"] != 32768 or config["max_new_tokens"] != 4096:
        raise ValueError("Sequence/response contracts changed")
    a = config["training_args"]
    if (
        a["per_device_train_batch_size"] != 1
        or a["per_device_eval_batch_size"] != 1
        or not a["bf16"]
        or a["fp16"]
        or a["report_to"]
        or a["push_to_hub"]
        or a["remove_unused_columns"]
        or not a["prediction_loss_only"]
    ):
        raise ValueError("Expected single-GPU BF16 private, pretokenized recipe")
    if (
        config["loss_contract"]
        != "family_task_cluster_trajectory_turn_balanced_sequence_mean"
    ):
        raise ValueError("Unsupported weighting objective")
    if (
        config["lora"]["target_modules"]
        != ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
        or config["lora"]["task_type"] != "CAUSAL_LM"
    ):
        raise ValueError("Unexpected LoRA targets")
    if config["profile"] == "smoke" and not 1 <= a["max_steps"] <= 4:
        raise ValueError("Smoke must remain bounded to <=4 optimizer steps")
    if config["profile"] == "smoke" and (
        config["smoke_selection"] not in {"shortest", "longest"}
        or not 1 <= config["smoke_examples"] <= 4
        or not 1 <= config["smoke_max_example_length"] <= 32768
    ):
        raise ValueError("Invalid complete-example smoke selection")
    return {
        "status": "CONFIG_VALID",
        "profile": config["profile"],
        "training_started": False,
    }


def selected_token_loss(model, inputs):
    """Pinned Qwen3 logits_to_keep contract; only compute logits needed by labels.

    Batch size one is intentional. Loss averages supervised tokens first, then
    applies the precomputed example weight. Trainer averages across microbatches.
    """
    import torch
    import torch.nn.functional as F

    values = dict(inputs)
    labels = values.pop("labels")
    weight = values.pop("example_weight")
    if labels.shape[0] != 1:
        raise ValueError("Recipe supports batch size one")
    positions = torch.nonzero(labels[0] != -100, as_tuple=False).flatten()
    if not len(positions) or positions.min() < 1:
        raise ValueError("Empty or unshiftable labels")
    output = model(**values, logits_to_keep=positions - 1, use_cache=False)
    loss = F.cross_entropy(
        output.logits[0].float(), labels[0, positions], reduction="mean"
    )
    return loss * weight[0], output


def collate(rows):
    import torch

    if len(rows) != 1:
        raise ValueError("Batch size one required; no implicit packing")
    r = rows[0]
    return {
        **{
            k: torch.tensor([r[k]], dtype=torch.long)
            for k in ["input_ids", "attention_mask", "labels"]
        },
        "example_weight": torch.tensor([r["example_weight"]], dtype=torch.float32),
    }


def preflight(config):
    validate_config(config)
    issues = []
    versions = {}
    for name, expected in config["packages"].items():
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "NOT_INSTALLED"
        versions[name] = version
        if version != expected:
            issues.append(name + "_VERSION_MISMATCH")
    if versions.get("torch") != "NOT_INSTALLED":
        import torch

        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            issues.append("ONE_CUDA_GPU_REQUIRED")
        else:
            props = torch.cuda.get_device_properties(0)
            if "RTX PRO 6000" not in props.name or props.total_memory < 90 * 1024**3:
                issues.append("REMOTE_96GB_GPU_NOT_VERIFIED")
            if not torch.cuda.is_bf16_supported():
                issues.append("BF16_NOT_SUPPORTED")
    return {
        "status": "BLOCKED" if issues else "READY_FOR_EXPLICIT_SMOKE",
        "issues": issues,
        "packages": versions,
        "training_started": False,
        "remote_training_pass": False,
    }


def tensor_hash(state):
    h = hashlib.sha256()
    for name, tensor in sorted(state.items()):
        h.update(name.encode())
        h.update(
            tensor.detach()
            .cpu()
            .contiguous()
            .view(__import__("torch").uint8)
            .numpy()
            .tobytes()
        )
    return h.hexdigest()


def gradient_health(model):
    import torch

    squared_norm, count = 0.0, 0
    for parameter in model.parameters():
        if not parameter.requires_grad or parameter.grad is None:
            continue
        grad = parameter.grad.detach().float()
        if not torch.isfinite(grad).all():
            raise ValueError("Nonfinite trainable gradient")
        squared_norm += float(grad.norm()) ** 2
        count += 1
    if not count or not math.isfinite(squared_norm) or squared_norm == 0:
        raise ValueError("No finite nonzero trainable gradient")
    return {"gradient_tensors": count, "l2_norm": math.sqrt(squared_norm)}


def execute(config, dataset, output):
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
    with torch.inference_mode():
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
    with torch.inference_mode():
        actual = (
            restored(**probe, logits_to_keep=1, use_cache=False).logits.float().cpu()
        )
    if not torch.allclose(expected, actual, atol=0.01, rtol=0.01):
        raise ValueError("Checkpoint reload numerical mismatch")
    result = {
        "status": "PASS",
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
