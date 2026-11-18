"""Bounded same-actor forward/backward comparison; never an optimizer update."""

import math
from pathlib import Path
import time

from .io import digest, read_json, write_json


def gradient_comparison(first, second):
    if first.keys() != second.keys():
        raise ValueError("Different trainable gradient names")
    norm, delta, maximum = 0.0, 0.0, 0.0
    for name, a in first.items():
        b = second[name]
        if a.shape != b.shape:
            raise ValueError("Different gradient shapes")
        a, b = a.double(), b.double()
        d = a - b
        norm += float(a.square().sum())
        delta += float(d.square().sum())
        maximum = max(maximum, float(d.abs().max()))
    return {
        "max_absolute_error": maximum,
        "l2_absolute_error": math.sqrt(delta),
        "l2_relative_error": math.sqrt(delta / norm) if norm else None,
        "reference_l2_norm": math.sqrt(norm),
        "before_global_clipping": True,
    }


def worker(request, output, *, merged):
    """A separate process for each side, loading the same frozen actor anew."""
    import torch
    from peft import get_peft_model_state_dict

    from .grpo_kernels import inspect_kernels
    from .sft_recipe import tensor_hash
    from .s2_logprob import audit_behavior
    from .student_tokens import KEY, REVISION
    from .v2_grpo import group_advantages, make_trainer, segments, update_batch
    from .v2_model import load_tokenizer
    from .v2_training import load_trainable, verify_checkpoint

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cfg, groups = request["config"], request["groups"]
    identity = cfg["models"][cfg["selected_model"]]
    receipt = verify_checkpoint(request["actor"], identity)
    before = receipt["adapter_sha256_after"]
    if before != request["actor_sha256"] or len(groups) != 1:
        raise ValueError(
            "Probe requires one fixed four-way group and its sampling actor"
        )
    expected_policy = {**identity, "checkpoint_id": digest({"adapter_sha256": before})}
    if cfg["grpo"]["beta"] != 0:
        raise ValueError("This bounded comparison retains the paused beta=0 recipe")
    for group in groups:
        if group_advantages(group) is None:
            raise ValueError("Invalid rewards; do not manufacture probe advantages")
        for e in group:
            if e["policy"] != expected_policy:
                raise ValueError("Sampling and comparison actors differ")
            rows = segments(e["directory"], e["policy"], max_length=65536)
            if any(r.get(KEY) != REVISION for r in rows):
                raise ValueError("Comparison requires fresh append-protocol receipts")
    kernels = inspect_kernels(required=cfg["grpo"].get("require_fast_kernels", False))
    model = load_trainable(identity, cfg["lora"], request["actor"])
    model.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    # Equal deterministic objective on both sides, independent model loads.
    for module in model.modules():
        if isinstance(module, torch.nn.Dropout) and module.p:
            raise ValueError("Disable stochastic dropout for the numerical comparison")
    settings = {**cfg["grpo"], "coalesce_exact_prefixes": merged}
    trainer = make_trainer(
        model, load_tokenizer(identity), output / "trainer", settings
    )
    audit = audit_behavior(trainer, groups, cfg["segment"]["logprob_audit"])
    write_json(output / "behavior_audit.json", audit)
    if not audit["passed"]:
        raise ValueError("Behavior probability definition/actor audit failed")
    gradients = {}

    def observe(current):
        gradients.update(
            {
                n: p.grad.detach().float().cpu().clone()
                for n, p in current.named_parameters()
                if p.requires_grad
            }
        )

    torch.manual_seed(42)
    torch.cuda.manual_seed_all(42)
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    start = time.monotonic()
    result = update_batch(
        trainer,
        groups,
        max_length=65536,
        optimizer_step=False,
        observe_gradients=observe,
        measure_constant=True,
    )
    torch.cuda.synchronize()
    elapsed = time.monotonic() - start
    if (
        tensor_hash(get_peft_model_state_dict(model)) != before
        or trainer.state.global_step
    ):
        raise ValueError("Diagnostic unexpectedly changed the actor")
    torch.save(gradients, output / "preclip_gradients.pt")
    result.update(
        mode="merged" if merged else "per_reply",
        actor_sha256=before,
        input_sha256=digest(request),
        optimizer_updates=0,
        wall_seconds=elapsed,
        includes_preclip_gradient_cpu_copy=True,
        gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
        gpu_peak_reserved_bytes=torch.cuda.max_memory_reserved(),
        kernels=kernels,
        zero_advantage_diagnostic=not any(group_advantages(groups[0])),
        actual_learning_rates=[g["lr"] for g in trainer.optimizer.param_groups],
        beta=trainer.beta,
    )
    write_json(output / "result.json", result)
    return result


def compare_outputs(root):
    import torch

    root = Path(root)
    a, b = [read_json(root / side / "result.json") for side in ("per_reply", "merged")]
    if any(
        a[k] != b[k]
        for k in ("actor_sha256", "input_sha256", "kernels", "ratio_token_count")
    ):
        raise ValueError("Comparison does not share tokens, actor and kernels")
    grads = [
        torch.load(
            root / side / "preclip_gradients.pt", map_location="cpu", weights_only=True
        )
        for side in ("per_reply", "merged")
    ]
    differences = gradient_comparison(*grads)
    return {
        "per_reply": a,
        "merged": b,
        "gradients": differences,
        "loss_absolute_error": abs(a["loss"] - b["loss"]),
        "observed_wall_ratio": a["wall_seconds"] / b["wall_seconds"],
        "optimizer_updates": 0,
        "note": "Same fresh group, independent same-weight loads; constant rewards give zero policy gradients and cannot validate a nonzero update.",
    }
