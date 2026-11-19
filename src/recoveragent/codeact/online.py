"""CodeAct behavior receipts and the existing clipped GRPO optimizer."""

import gc
import json
import math
import os
from pathlib import Path

from recoveragent.demonstrations import sha256
from recoveragent.io import digest, read_json, write_json
from recoveragent.sft_recipe import tensor_hash
from recoveragent.v2_grpo import group_advantages, make_trainer, update_batch
from recoveragent.v2_model import load_tokenizer
from recoveragent.v2_training import (
    finalize_reload,
    finish_checkpoint,
    load_trainable,
    verify_checkpoint,
)

from .protocol import identity, now

REVISION = "codeact_grpo_v1"
LOGPROBS = "vllm_processed_logprobs_temperature_top_p1_top_k_all"


def policy(receipt, config):
    return {
        **config["model"],
        "checkpoint_id": digest({"adapter_sha256": receipt["adapter_sha256_after"]}),
    }


def segments(directory, expected_policy, *, max_length=65536):
    rows = []
    for path in sorted((Path(directory) / "requests").glob("*.tokens.json")):
        row = read_json(path)
        behavior = row.get("behavior", {})
        request = read_json(
            path.with_name(path.name.replace(".tokens.json", ".request.json"))
        )
        raw = read_json(
            path.with_name(path.name.replace(".tokens.json", ".backend_response.json"))
        )
        dispatched = read_json(
            path.with_name(path.name.replace(".tokens.json", ".backend_request.json"))
        )
        lp = behavior.get("logprobs")
        if (
            behavior.get("policy") != expected_policy
            or behavior.get("protocol") != identity()
            or behavior.get("logprob_contract") != LOGPROBS
            or behavior.get("top_p") != 1.0
            or not isinstance(lp, list)
            or len(lp) != len(row["generated_ids"])
            or not lp
            or any(not math.isfinite(x) for x in lp)
            or row["input_ids"] != request["input_token_ids"]
            or row["generated_ids"] != raw["choices"][0]["token_ids"]
            or lp != raw["choices"][0]["logprobs"]["token_logprobs"]
            or behavior["temperature"] != request["settings"]["temperature"]
            or dispatched["prompt"] != row["input_ids"]
            or dispatched["temperature"] != behavior["temperature"]
            or dispatched["top_p"] != 1.0
            or dispatched["top_k"] != -1
            or dispatched.get("logprobs") != 1
            or raw["model"] != dispatched["model"]
            or len(row["input_ids"]) + len(lp) > max_length
        ):
            raise ValueError("CodeAct rollout token/policy/logprob contract mismatch")
        rows.append(
            {
                "prompt_ids": row["input_ids"],
                "completion_ids": row["generated_ids"],
                "logprobs": lp,
                "request_sha256": digest(request),
                "response_sha256": digest(row),
                "temperature": behavior["temperature"],
                "top_p": 1.0,
                "logprob_contract": LOGPROBS,
                "student_input_encoding": identity()["revision"],
            }
        )
    if not rows:
        raise ValueError("No CodeAct behavior receipts")
    return rows


def target_logps(model, ids, attention, positions, temperature, chunk_size=128):
    """One body forward, checkpointed vocabulary reductions only at sampled positions."""
    import torch
    from torch.utils.checkpoint import checkpoint

    base = model.get_base_model() if hasattr(model, "get_base_model") else model
    hidden = base.model(
        input_ids=ids, attention_mask=attention, use_cache=False
    ).last_hidden_state

    def reduce(values, targets):
        logits = base.lm_head(values).float() / temperature
        logps = logits.log_softmax(-1)
        chosen = logps.gather(-1, targets.unsqueeze(-1)).squeeze(-1)
        with torch.no_grad():
            entropy = -(logps.exp() * logps).sum(-1)
        return chosen, entropy

    pairs = []
    for offset in range(0, len(positions), chunk_size):
        selected = positions[offset : offset + chunk_size]
        args = (hidden[:, selected], ids[:, selected + 1])
        pairs.append(
            checkpoint(reduce, *args, use_reentrant=False)
            if torch.is_grad_enabled()
            else reduce(*args)
        )
    return tuple(torch.cat([p[i] for p in pairs], dim=1) for i in range(2))


def update(config, request, output):
    import torch
    from peft import get_peft_model_state_dict

    from recoveragent.grpo_kernels import inspect_kernels
    from recoveragent.s2_logprob import audit_behavior

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    parent = verify_checkpoint(request["actor"], config["model"])
    if parent.get("protocol") != identity() or not (
        parent.get("formal_s0") or parent.get("stage") == REVISION
    ):
        raise ValueError("GRPO must continue new CodeAct weights")
    groups = request["groups"]
    if not 1 <= len(groups) <= 2 or any(
        not group_advantages(g) or not any(group_advantages(g)) for g in groups
    ):
        raise ValueError("Only one or two complete varying groups may update")
    if any(e["policy"] != policy(parent, config) for g in groups for e in g):
        raise ValueError("Pending groups do not belong to the unchanged current actor")
    if config["grpo"]["beta"] != 0:
        raise ValueError("This bounded CodeAct run uses the approved zero-KL recipe")
    kernels = inspect_kernels(required=True)
    write_json(output / "kernels.json", kernels)
    torch.manual_seed(config["seed"])
    model = load_trainable(config["model"], config["lora"], request["actor"])
    model.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    tokenizer = load_tokenizer(config["model"])
    before = tensor_hash(get_peft_model_state_dict(model))
    trainer = make_trainer(model, tokenizer, output / "trainer", config["grpo"])
    trainer.state.global_step = request["optimizer_steps"]
    if request.get("optimizer"):
        if sha256(request["optimizer"]) != request["optimizer_sha256"]:
            raise ValueError("Optimizer SHA mismatch")
        saved = torch.load(request["optimizer"], map_location="cpu", weights_only=False)
        if (
            saved["actor_sha256"] != before
            or saved["steps"] != request["optimizer_steps"]
        ):
            raise ValueError("Optimizer is not paired with the actor")
        trainer.optimizer.load_state_dict(saved["optimizer"])
        torch.set_rng_state(saved["torch_rng"])
        torch.cuda.set_rng_state_all(saved["cuda_rng"])
    for group in trainer.optimizer.param_groups:
        group["lr"] = config["grpo"]["learning_rate"]
    if request["optimizer_steps"] == 0:
        audit = audit_behavior(
            trainer, groups, config["online"]["logprob_audit"], segment_loader=segments
        )
        write_json(output / "behavior_audit.json", audit)
        if not audit["passed"]:
            raise ValueError("Same-actor behavior probability audit failed")
    started = now()
    torch.cuda.reset_peak_memory_stats()
    allocator = {
        "requested": config["grpo"].get("cuda_allocator"),
        "environment": {
            k: os.environ.get(k)
            for k in ("PYTORCH_ALLOC_CONF", "PYTORCH_CUDA_ALLOC_CONF")
        },
        "backend": torch.cuda.memory.get_allocator_backend(),
    }
    write_json(output / "allocator.json", allocator)

    def observe_memory(event):
        free, total = torch.cuda.mem_get_info()
        record = {
            **event,
            "elapsed_seconds": now() - started,
            "allocated_bytes": torch.cuda.memory_allocated(),
            "reserved_bytes": torch.cuda.memory_reserved(),
            "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
            "device_free_bytes": free,
            "device_total_bytes": total,
        }
        with (output / "memory.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
            stream.flush()

    health = update_batch(
        trainer,
        groups,
        max_length=65536,
        segment_loader=segments,
        observe_memory=observe_memory if config["grpo"].get("record_memory") else None,
    )
    metadata = {
        "protocol": identity(),
        "stage": REVISION,
        "formal_s0": False,
        "optimizer_steps": trainer.state.global_step,
        "s0": request["s0"],
        "learning_rate": [g["lr"] for g in trainer.optimizer.param_groups],
        "beta": trainer.beta,
        "group_ids": request["group_ids"],
        "training_wall_seconds": now() - started,
        "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "config_sha256": digest(config),
        "cuda_allocator": allocator,
    }
    ids = segments(groups[0][0]["directory"], groups[0][0]["policy"])[0]["prompt_ids"][
        :128
    ]
    actor = output / "actor"
    after, probe = finish_checkpoint(
        model,
        tokenizer,
        actor,
        config["model"],
        before,
        parent=request["actor"],
        metadata=metadata,
        probe_ids=ids,
    )
    optimizer = output / "optimizer.pt"
    torch.save(
        {
            "actor_sha256": after,
            "steps": trainer.state.global_step,
            "optimizer": trainer.optimizer.state_dict(),
            "torch_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state_all(),
        },
        optimizer,
    )
    del trainer, model
    gc.collect()
    torch.cuda.empty_cache()
    finalize_reload(
        actor, config["model"], before, after, probe, ids, request["actor"], metadata
    )
    result = {
        **health,
        **metadata,
        "actor": str(actor),
        "optimizer": str(optimizer),
        "optimizer_sha256": sha256(optimizer),
        "reload_passed": True,
    }
    write_json(output / "result.json", result)
    return result
