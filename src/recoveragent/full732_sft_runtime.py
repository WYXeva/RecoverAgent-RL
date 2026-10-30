"""Full732 BF16 runtime: reuse sealed loss/helpers; no Trainer validation path."""

import argparse
import copy
import gc
import hashlib
import json
import math
import os
import shutil
import time
from pathlib import Path

from .base_sft import MODEL, REVISION, load_tokenizer
from .demonstrations import sha256
from .full732_sft_data import FORMAT, LOSS, MANIFEST, ROOT, json_rows, verify_prepared
from .io import digest, files_manifest, read_json, write_json
from .sft_recipe import collate, gradient_health, preflight as old_preflight
from .sft_recipe import (
    selected_token_loss,
    tensor_hash,
    validate_config as old_validate,
)


def validate_config(config):
    legacy = copy.deepcopy(config)
    legacy["loss_contract"] = (
        "family_task_cluster_trajectory_turn_balanced_sequence_mean"
    )
    legacy["profile"] = "base_sft" if config["profile"] == "formal" else "smoke"
    old_validate(legacy)
    profile = config["profile"]
    if profile not in {"short_smoke", "long_smoke", "formal"}:
        raise ValueError("Unknown Full732 training stage")
    expected = read_json(ROOT / "configs" / f"full732_m1_{profile}.json")
    if (
        config != expected
        or config["runtime_contract"] != FORMAT
        or config["loss_contract"] != LOSS
    ):
        raise ValueError("Use the frozen Full732 recipe")
    args = config["training_args"]
    if (
        args["eval_strategy"] != "no"
        or args["do_eval"]
        or args["load_best_model_at_end"]
    ):
        raise ValueError("Trainer validation/model selection must remain disabled")
    return legacy


def verify_lock():
    path = ROOT / "configs/full732_m1_runtime_lock.json"
    lock = read_json(path)
    if lock["runtime_contract"] != FORMAT:
        raise ValueError("Unexpected runtime contract")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("Full732 runtime source lock differs: " + name)
    return sha256(path)


def preflight(config, output):
    legacy = validate_config(config)
    check = old_preflight(legacy)
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        check["issues"].append("OFFLINE_CACHE_REQUIRED")
    free = shutil.disk_usage(Path(output).parent).free
    check["disk_free_bytes"] = free
    if free < 20 * 1024**3:
        check["issues"].append("LESS_THAN_20_GIB_FREE")
    if check["issues"]:
        check["status"] = "BLOCKED"
        return check
    import torch
    from huggingface_hub import snapshot_download

    snapshot = Path(snapshot_download(MODEL, revision=REVISION, local_files_only=True))
    if snapshot.name != REVISION:
        raise ValueError("Pinned model snapshot required")
    trusted = read_json(ROOT / "reports/p3b_model_integrity_20261023.json")
    if (trusted["model"], trusted["revision"]) != (MODEL, REVISION):
        raise ValueError("Base model reference differs")
    # This reference contains raw base-model hashes only, never old SFT data.
    check["model_files"] = {}
    for name, expected in trusted["files"].items():
        path = snapshot / name
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
                h.update(chunk)
        if (
            path.stat().st_size != expected["bytes"]
            or h.hexdigest() != expected["sha256"]
        ):
            raise ValueError("Pinned raw model file differs: " + name)
        check["model_files"][name] = h.hexdigest()
    props = torch.cuda.get_device_properties(0)
    check.update(
        gpu=props.name,
        total_memory_bytes=props.total_memory,
        cuda_runtime=torch.version.cuda,
        snapshot=str(snapshot),
    )
    return check


def select_examples(rows, config):
    if config["profile"] == "formal":
        return rows
    rows = sorted(
        (r for r in rows if len(r["input_ids"]) <= config["smoke_max_example_length"]),
        key=lambda r: len(r["input_ids"]),
        reverse=config["smoke_selection"] == "longest",
    )[: config["smoke_examples"]]
    if len(rows) != config["smoke_examples"]:
        raise ValueError("Insufficient complete smoke examples")
    return rows


def check_prerequisites(profile, paths, prepared_sha, lock_sha):
    stages = {
        "short_smoke": [],
        "long_smoke": ["short_smoke"],
        "formal": ["short_smoke", "long_smoke"],
    }[profile]
    if len(paths) != len(stages):
        raise ValueError("Required preceding smoke gates missing")
    receipts = []
    for path, stage in zip(paths, stages, strict=True):
        path = Path(path)
        receipt = read_json(path)
        expected_config = read_json(ROOT / "configs" / f"full732_m1_{stage}.json")
        if (
            any(
                receipt.get(k) != value
                for k, value in {
                    "status": "PASS",
                    "profile": stage,
                    "runtime_contract": FORMAT,
                    "source_manifest_sha256": MANIFEST,
                    "prepared_manifest_sha256": prepared_sha,
                    "runtime_lock_sha256": lock_sha,
                    "config_sha256": digest(expected_config),
                    "checkpoint_reloaded": True,
                    "parameter_update_and_reload_pass": True,
                    "optimizer_steps": 2 if stage == "short_smoke" else 1,
                }.items()
            )
            or receipt["trainable_before_hash"] == receipt["trainable_after_hash"]
            or (receipt["trainable_after_hash"] != receipt["restored_trainable_hash"])
        ):
            raise ValueError(
                "Preceding smoke did not pass the same data/runtime contract"
            )
        actual = files_manifest(path.parent)
        actual.pop(path.name)
        if actual != receipt["files"]:
            raise ValueError("Preceding smoke evidence/checkpoint changed")
        receipts.append({"profile": stage, "receipt_sha256": sha256(path)})
    return receipts


def execute(config, dataset, output, prepared_sha, prerequisites=()):
    output = Path(output)
    if (
        output.resolve() == Path(dataset).resolve()
        or Path(dataset).resolve() in output.resolve().parents
    ):
        raise ValueError("Output must not modify prepared data")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    write_json(output / "run_config.json", config)
    lock_sha = verify_lock()
    validate_config(config)
    prior = check_prerequisites(
        config["profile"], prerequisites, prepared_sha, lock_sha
    )
    data = verify_prepared(dataset, prepared_sha)
    check = preflight(config, output)
    write_json(output / "preflight.json", check)
    if check["issues"]:
        raise ValueError("Remote hardware/package/cache/disk preflight failed")
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model, get_peft_model_state_dict
    from transformers import (
        AutoModelForCausalLM,
        Trainer,
        TrainerCallback,
        TrainingArguments,
        set_seed,
    )

    tokenizer = load_tokenizer()
    train = select_examples(list(json_rows(Path(dataset) / "train.jsonl.gz")), config)
    write_json(
        output / "example_selection.json",
        [
            {
                k: r[k]
                for k in (
                    "task_id",
                    "trajectory_id",
                    "assistant_index",
                    "representation",
                )
            }
            | {"sequence_tokens": len(r["input_ids"])}
            for r in train
        ],
    )
    set_seed(config["training_args"]["seed"])
    model = AutoModelForCausalLM.from_pretrained(
        MODEL,
        revision=REVISION,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    if model.config._commit_hash != REVISION:
        raise ValueError("Loaded model revision differs")
    model = get_peft_model(model, LoraConfig(**config["lora"]))
    if any(p.requires_grad and "lora_" not in n for n, p in model.named_parameters()):
        raise ValueError("Raw base weights must remain frozen")
    before = tensor_hash(get_peft_model_state_dict(model))
    write_json(output / "initial_parameters.json", {"trainable_hash": before})
    gradients, logs = [], []

    def append_event(name, value):
        with (output / name).open("a") as stream:
            stream.write(json.dumps(value, allow_nan=False) + "\n")
            stream.flush()

    class WeightedTrainer(Trainer):
        def compute_loss(
            self, model, inputs, return_outputs=False, num_items_in_batch=None
        ):
            loss, outputs = selected_token_loss(model, inputs)
            if not torch.isfinite(loss).all():
                raise ValueError("Nonfinite microbatch loss")
            return (loss, outputs) if return_outputs else loss

        def evaluate(self, *args, **kwargs):
            raise RuntimeError("Full732 M1 Trainer evaluation is disabled")

    class Health(TrainerCallback):
        def on_pre_optimizer_step(self, args, state, control, model=None, **kwargs):
            entry = {"step": state.global_step + 1, **gradient_health(model)}
            gradients.append(entry)
            append_event("gradients.jsonl", entry)

        def on_log(self, args, state, control, logs=None, **kwargs):
            entry = {"step": state.global_step, **(logs or {})}
            if any(
                isinstance(v, float) and not math.isfinite(v) for v in entry.values()
            ):
                raise ValueError("Nonfinite Trainer metric")
            append_event("loss_curve.jsonl", entry)

    class CountedCollator:
        def __call__(self, rows):
            logs.append((rows[0]["trajectory_id"], rows[0]["assistant_index"]))
            return collate(rows)

    args = TrainingArguments(output_dir=str(output), **config["training_args"])
    trainer = WeightedTrainer(
        model=model,
        args=args,
        train_dataset=train,
        eval_dataset=None,
        data_collator=CountedCollator(),
        processing_class=tokenizer,
        callbacks=[Health()],
    )
    trainer.model_accepts_loss_kwargs = False
    batches = len(trainer.get_train_dataloader())
    expected_steps = math.ceil(batches / args.gradient_accumulation_steps)
    if config["profile"] != "formal":
        expected_steps = args.max_steps
    write_json(
        output / "dataloader.json",
        {
            "samples": len(train),
            "batches": batches,
            "expected_optimizer_steps": expected_steps,
            "evaluation_strategy": str(args.eval_strategy),
            "eval_dataset": None,
        },
    )
    torch.cuda.reset_peak_memory_stats()
    write_json(output / "training_started.json", {"started_unix": time.time()})
    training_started = time.monotonic()
    training = trainer.train()
    torch.cuda.synchronize()
    train_seconds = time.monotonic() - training_started
    after = tensor_hash(get_peft_model_state_dict(model))
    if (
        len(gradients) != expected_steps
        or trainer.state.global_step != expected_steps
        or (before == after or not math.isfinite(training.metrics["train_loss"]))
    ):
        raise ValueError("Incomplete or nonfinite parameter update")
    expected_seen = [(r["trajectory_id"], r["assistant_index"]) for r in train]
    if sorted(logs) != sorted(expected_seen):
        raise ValueError(
            "Dataloader did not consume each complete example exactly once"
        )
    trainer.save_model()
    trainer.save_state()
    tokenizer.save_pretrained(output)
    # Preserve final optimizer/scheduler/RNG state, including a partial final
    # accumulation group. This does not claim that exact resume was tested.
    trainer._save_checkpoint(model, trial=None)
    probe = {
        k: v.to(model.device)
        for k, v in collate([train[0]]).items()
        if k in ("input_ids", "attention_mask")
    }
    model.eval()
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        expected = (
            model(**probe, logits_to_keep=1, use_cache=False).logits.float().cpu()
        )
    peak = torch.cuda.max_memory_allocated()
    reserved = torch.cuda.max_memory_reserved()
    del trainer, model
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
        raise ValueError("Restored LoRA tensors differ")
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        actual = (
            restored(**probe, logits_to_keep=1, use_cache=False).logits.float().cpu()
        )
    if (
        not torch.isfinite(expected).all()
        or not torch.isfinite(actual).all()
        or not torch.allclose(expected, actual, atol=0.01, rtol=0.01)
    ):
        raise ValueError("Reload logits verification failed")
    result = {
        "status": "PASS",
        "runtime_contract": FORMAT,
        "profile": config["profile"],
        "source_manifest_sha256": MANIFEST,
        "prepared_manifest_sha256": prepared_sha,
        "runtime_lock_sha256": lock_sha,
        "config_sha256": digest(config),
        "model": MODEL,
        "revision": REVISION,
        "prerequisites": prior,
        "trainable_before_hash": before,
        "trainable_after_hash": after,
        "restored_trainable_hash": restored_hash,
        "checkpoint_reloaded": True,
        "parameter_update_and_reload_pass": True,
        "optimizer_steps": len(gradients),
        "gradient_checks": gradients,
        "metrics": training.metrics,
        "reload_max_abs_error": float((expected - actual).abs().max()),
        "peak_allocated_bytes": max(peak, torch.cuda.max_memory_allocated()),
        "peak_reserved_bytes": max(reserved, torch.cuda.max_memory_reserved()),
        "training_wall_seconds": train_seconds,
        "total_wall_seconds": time.monotonic() - started,
        "training_examples": len(train),
        "source_trajectories": data["trajectories"],
        "training_validation": None,
        "C244_evaluated": False,
        "files": files_manifest(output),
    }
    write_json(output / "recoveragent_checkpoint.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--prepared-manifest-sha256", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--prerequisite", action="append", default=[])
    args = parser.parse_args()
    execute(
        read_json(args.config),
        args.dataset,
        args.output,
        args.prepared_manifest_sha256,
        args.prerequisite,
    )


if __name__ == "__main__":
    main()
