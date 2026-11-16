"""One atomic two-group GRPO update, fixed S0 KL reference and optimizer carry."""

import gc
import random
import time
from pathlib import Path

from .demonstrations import sha256
from .io import digest, write_json
from .sft_recipe import tensor_hash
from .v2_grpo import group_advantages, make_trainer, segments, update_batch
from .v2_training import (
    load_trainable,
    finish_checkpoint,
    finalize_reload,
    verify_checkpoint,
)


def update(config, request, output, *, device="cuda"):
    import numpy as np
    import torch
    from peft import get_peft_model_state_dict
    from .v2_model import load_tokenizer

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    groups = request["groups"]
    if len(groups) != 2:
        raise ValueError("S2 updates require exactly two scheduled groups")
    parent, reference = request["actor"], request["s0"]
    identity = config["models"][config["selected_model"]]
    parent_receipt = verify_checkpoint(parent, identity)
    source = verify_checkpoint(reference, identity)
    if source["adapter_sha256_after"] != config["s0"]["adapter_sha256"]:
        raise ValueError("KL reference must be the accepted common S0")
    expected_id = digest({"adapter_sha256": parent_receipt["adapter_sha256_after"]})
    for group in groups:
        for row in group:
            if row["policy"] is None and row["success"] is None:
                continue
            if row["policy"]["checkpoint_id"] != expected_id:
                raise ValueError("Rollout is not the current actor")
    complete = [
        g
        for g in groups
        if all(
            type(e["success"]) is bool and e["classification"] != "infrastructure_error"
            for e in g
        )
    ]
    varying = [g for g in complete if (a := group_advantages(g)) is not None and any(a)]
    if not varying:
        result = {
            "updated": False,
            "reason": "no_scorable_reward_variation",
            "actor": parent,
            "optimizer": request.get("optimizer"),
            "optimizer_steps": request["optimizer_steps"],
        }
        write_json(output / "result.json", result)
        return result
    if not request.get("optimizer"):
        random.seed(config["s2"]["seed"])
        np.random.seed(config["s2"]["seed"])
        torch.manual_seed(config["s2"]["seed"])
    # Constant-reward groups do not receive a KL-only update.
    model = load_trainable(identity, config["lora"], parent, device=device)
    model.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    before = tensor_hash(get_peft_model_state_dict(model))
    model.load_adapter(reference, adapter_name="coevolve_reference", is_trainable=False)
    model.set_adapter("default")
    reference_hash = tensor_hash(
        get_peft_model_state_dict(model, adapter_name="coevolve_reference")
    )
    if reference_hash != source["adapter_sha256_after"]:
        raise ValueError("Loaded KL reference differs from S0")
    tokenizer = load_tokenizer(identity)
    trainer = make_trainer(model, tokenizer, output / "trainer", config["grpo"])
    trainer.state.global_step = request["optimizer_steps"]
    if request.get("optimizer"):
        old = Path(request["optimizer"])
        if sha256(old) != request["optimizer_sha256"]:
            raise ValueError("Optimizer file changed")
        saved = torch.load(old, map_location="cpu", weights_only=False)
        if (
            saved["actor_sha256"] != before
            or saved["steps"] != request["optimizer_steps"]
        ):
            raise ValueError("Optimizer belongs to a different actor or update")
        trainer.optimizer.load_state_dict(saved["optimizer"])
        random.setstate(saved["python_rng"])
        np.random.set_state(saved["numpy_rng"])
        torch.set_rng_state(saved["torch_rng"])
        if device == "cuda":
            torch.cuda.set_rng_state_all(saved["cuda_rng"])
    started = time.monotonic()
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    health = update_batch(trainer, varying, max_length=65536)
    if (
        tensor_hash(get_peft_model_state_dict(model, adapter_name="coevolve_reference"))
        != reference_hash
    ):
        raise ValueError("Frozen S0 reference changed")
    model.delete_adapter("coevolve_reference")
    probe_ids = segments(
        varying[0][0]["directory"], varying[0][0]["policy"], max_length=65536
    )[0]["prompt_ids"][:128]
    actor = output / "actor"
    after, probe = finish_checkpoint(
        model,
        tokenizer,
        actor,
        identity,
        before,
        parent=parent,
        metadata={},
        probe_ids=probe_ids,
    )
    optimizer = output / "optimizer.pt"
    torch.save(
        {
            "actor_sha256": after,
            "steps": trainer.state.global_step,
            "optimizer": trainer.optimizer.state_dict(),
            "python_rng": random.getstate(),
            "numpy_rng": np.random.get_state(),
            "torch_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state_all() if device == "cuda" else [],
        },
        optimizer,
    )
    metadata = {
        "stage": "S2_grpo",
        "optimizer_steps": trainer.state.global_step,
        "reference_adapter_sha256": reference_hash,
        "beta": trainer.beta,
        "learning_rate": config["grpo"]["learning_rate"],
        "group_ids": request["group_ids"],
        "training_wall_seconds": time.monotonic() - started,
        "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated()
        if device == "cuda"
        else 0,
        "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved()
        if device == "cuda"
        else 0,
    }
    del trainer, model
    gc.collect()
    torch.cuda.empty_cache()
    finalize_reload(actor, identity, before, after, probe, probe_ids, parent, metadata)
    result = {
        **health,
        **metadata,
        "actor": str(actor),
        "optimizer": str(optimizer),
        "optimizer_sha256": sha256(optimizer),
        "actor_sha256": after,
        "reload_passed": True,
    }
    write_json(output / "result.json", result)
    return result
