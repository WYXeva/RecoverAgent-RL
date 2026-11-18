"""Fresh official-base LoRA SFT; one body forward per complete multi-turn sequence."""

from contextlib import nullcontext
import gc
import math
from pathlib import Path
import random

from recoveragent.io import write_json
from recoveragent.sft_recipe import collate, gradient_health, tensor_hash
from recoveragent.v2_model import load_tokenizer
from recoveragent.v2_training import load_trainable, finish_checkpoint, finalize_reload

from .data import load_dataset
from .protocol import identity, now


def sequence_loss(model, inputs, chunk_size=128):
    """Exact target-token mean CE; checkpoint LM-head chunks, never all-token logits."""
    import torch
    import torch.nn.functional as F
    from torch.utils.checkpoint import checkpoint

    labels = inputs["labels"]
    positions = (labels[0] != -100).nonzero().flatten()
    if labels.shape[0] != 1 or not len(positions) or int(positions.min()) == 0:
        raise ValueError("Expected one sequence with shiftable assistant targets")
    base = model.get_base_model() if hasattr(model, "get_base_model") else model
    hidden = base.model(
        input_ids=inputs["input_ids"],
        attention_mask=inputs["attention_mask"],
        use_cache=False,
    ).last_hidden_state

    def reduce(values, targets):
        return F.cross_entropy(base.lm_head(values).float(), targets, reduction="sum")

    total = hidden.new_zeros((), dtype=torch.float32)
    for start in range(0, len(positions), chunk_size):
        selected = positions[start : start + chunk_size]
        args = (hidden[0, selected - 1], labels[0, selected])
        total = total + (
            checkpoint(reduce, *args, use_reentrant=False)
            if torch.is_grad_enabled()
            else reduce(*args)
        )
    return total / len(positions) * inputs["example_weight"][0]


def _pass(config, rows, manifest, output, profile):
    import torch
    from peft import get_peft_model_state_dict
    from transformers import set_seed

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    stage = config["training"]
    seed = stage["seed"]
    set_seed(seed)
    # No parent path, auto-discovery or old optimizer loading is possible here.
    model = load_trainable(config["model"], config["lora"], parent=None)
    tokenizer = load_tokenizer(config["model"])
    model.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    model.train()
    before = tensor_hash(get_peft_model_state_dict(model))
    optimizer = torch.optim.AdamW(
        (p for p in model.parameters() if p.requires_grad),
        lr=stage["learning_rate"],
        weight_decay=0.0,
    )
    accumulation = stage["gradient_accumulation_steps"] if profile == "formal" else 1
    order = list(range(len(rows)))
    random.Random(seed).shuffle(order)
    order *= stage["epochs"] if profile == "formal" else 1
    expected = math.ceil(len(order) / accumulation)
    metadata = {
        "protocol": identity(),
        "profile": profile,
        "formal_s0": profile == "formal",
        "config": config,
        "data_manifest": manifest,
        "parent": None,
        "optimizer_restored": False,
        "optimizer": {"name": "AdamW", "weight_decay": 0.0, "lr_schedule": "constant"},
        "expected_optimizer_steps": expected,
        "loss_normalization": manifest["loss"],
    }
    write_json(output / "training_config.json", metadata)
    started = now()
    torch.cuda.reset_peak_memory_stats()
    steps, gradients, losses = 0, [], []
    try:
        for offset in range(0, len(order), accumulation):
            batch = order[offset : offset + accumulation]
            optimizer.zero_grad(set_to_none=True)
            step_start, batch_loss = now(), 0.0
            for index in batch:
                inputs = {
                    k: v.to(model.device) for k, v in collate([rows[index]]).items()
                }
                context = (
                    torch.autograd.graph.save_on_cpu(pin_memory=True)
                    if stage["activation_offload"] == "cpu"
                    else nullcontext()
                )
                with context, torch.autocast("cuda", dtype=torch.bfloat16):
                    loss = sequence_loss(model, inputs)
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite SFT loss")
                batch_loss += float(loss.detach()) / len(batch)
                (loss / len(batch)).backward()
            health = gradient_health(model)
            torch.nn.utils.clip_grad_norm_(
                (p for p in model.parameters() if p.requires_grad),
                stage["max_grad_norm"],
                error_if_nonfinite=True,
            )
            optimizer.step()
            steps += 1
            event = {
                "step": steps,
                "loss": batch_loss,
                **health,
                "seconds": now() - step_start,
                "learning_rates": [g["lr"] for g in optimizer.param_groups],
            }
            write_json(output / "updates" / f"{steps:05d}.json", event)
            gradients.append(health)
            losses.append(batch_loss)
        if steps != expected:
            raise ValueError(
                "Actual optimizer updates differ from the dataloader schedule"
            )
        metadata.update(
            optimizer_steps=steps,
            losses=losses,
            gradients=gradients,
            seconds=now() - started,
            gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
            gpu_peak_reserved_bytes=torch.cuda.max_memory_reserved(),
        )
        checkpoint_dir = output / "adapter"
        probe_ids = rows[0]["input_ids"][:128]
        after, probe = finish_checkpoint(
            model,
            tokenizer,
            checkpoint_dir,
            config["model"],
            before,
            parent=None,
            metadata=metadata,
            probe_ids=probe_ids,
        )
        torch.save(optimizer.state_dict(), output / "optimizer.pt")
        del model, optimizer, inputs, loss
        gc.collect()
        torch.cuda.empty_cache()
        receipt = finalize_reload(
            checkpoint_dir,
            config["model"],
            before,
            after,
            probe,
            probe_ids,
            None,
            metadata,
        )
        write_json(output / "result.json", receipt)
        return receipt
    except BaseException as exc:
        write_json(
            output / "failure.json",
            {
                "type": type(exc).__name__,
                "error": str(exc),
                "optimizer_steps": steps,
                "seconds": now() - started,
                "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            },
        )
        raise


def train(config, dataset, output, *, smoke=False):
    import torch

    if (
        config["training"]["parent_adapter"] is not None
        or config["training"]["resume_optimizer"]
    ):
        raise ValueError(
            "New S0 must start from official base, never an old adapter/optimizer"
        )
    rows, manifest = load_dataset(dataset, config)
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise ValueError("BF16 GPU required; no real model update was performed")
    output = Path(output)
    if output.exists():
        raise FileExistsError("Use a new training output directory")
    short = sorted(rows, key=lambda r: len(r["input_ids"]))[:2]
    _pass(config, short, manifest, output / "short_smoke", "short")
    _pass(
        config,
        [max(rows, key=lambda r: len(r["input_ids"]))],
        manifest,
        output / "long_smoke",
        "long",
    )
    if not smoke:
        _pass(config, rows, manifest, output / "S0_base", "formal")
