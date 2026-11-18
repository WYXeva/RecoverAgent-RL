"""Current-policy AppWorld groups, using TRL's clipped GRPO loss and optimizer.

Dynamic discovery changes the next request's tool catalogue. Consequently a
multi-turn episode is NOT necessarily one append-only token sequence. Opt-in
coalescing joins only exact token-prefix extensions, with unchanged target
logprobs and episode normalization. Tools/context never enter the loss.
"""

import gc
import math
import time
from contextlib import nullcontext
from pathlib import Path

from .grpo_segments import coalesce, offload_row
from .io import digest, read_json, write_json
from .reward_validity import valid_reward
from .sft_recipe import gradient_health, tensor_hash
from .v2_context import sequence_limit, validate_request_context
from .v2_inference import policy_id
from .v2_training import finalize_reload, finish_checkpoint, load_trainable


class NoPolicyUpdate(ValueError):
    """All bounded groups finished without a usable relative-reward signal."""


def group_advantages(episodes):
    if len(episodes) != 4:
        raise ValueError("A GRPO group needs four real attempts")
    keys = {
        digest(
            {
                k: e[k]
                for k in (
                    "task_id",
                    "generated_task_id",
                    "initial_state_hash",
                    "policy",
                )
            }
        )
        for e in episodes
    }
    if len(keys) != 1:
        raise ValueError("GRPO task, initial state or policy differs within group")
    if not all(valid_reward(e) for e in episodes):
        return None
    rewards = [float(e["success"]) for e in episodes]
    mean = sum(rewards) / 4
    std = math.sqrt(sum((r - mean) ** 2 for r in rewards) / 3)
    return [(r - mean) / (std + 1e-4) for r in rewards]


def segments(directory, expected_policy, *, max_length=None):
    max_length = sequence_limit() if max_length is None else max_length
    rows = []
    for path in sorted((Path(directory) / "session/requests").glob("*.request.json")):
        request = read_json(path)
        response = read_json(
            path.with_name(path.name.replace(".request.json", ".response.json"))
        )
        if (
            request["policy"] != expected_policy
            or response["policy"] != expected_policy
            or response["input_token_ids"] != request["input_token_ids"]
            or len(response["logprobs"]) != len(response["token_ids"])
        ):
            raise ValueError("Rollout policy/token/logprob receipt mismatch")
        if max_length > 32768 or "max_input_tokens" in request:
            validate_request_context(request, max_length)
        if len(request["input_token_ids"]) + len(response["token_ids"]) > max_length:
            raise ValueError("GRPO segment exceeds context; no truncation")
        rows.append(
            {
                "prompt_ids": request["input_token_ids"],
                "completion_ids": response["token_ids"],
                "logprobs": response["logprobs"],
                "request_sha256": digest(request),
                "response_sha256": digest(response),
                "top_p": request.get("top_p"),
                "temperature": request.get("temperature"),
                "logprob_contract": response.get("logprob_contract"),
            }
        )
    if not rows:
        raise ValueError("Scorable episode has no behavior receipts")
    return rows


def trainer_class():
    import torch
    from trl import GRPOTrainer

    class AppWorldGRPO(GRPOTrainer):
        def _get_per_token_logps_and_entropies(
            self, model, input_ids, attention_mask, logits_to_keep, **kwargs
        ):
            # Keep the mature TRL objective, but score the actual request exactly.
            # No assumption that changed dynamic schemas can be concatenated.
            mask = kwargs.get("sampling_mask")
            selected = (
                torch.arange(logits_to_keep, device=input_ids.device)
                if mask is None
                else mask[0].bool().nonzero().flatten()
            )
            if input_ids.shape[0] != 1 or not len(selected):
                raise ValueError("Request scoring needs one nonempty target sequence")
            positions = input_ids.shape[1] - logits_to_keep - 1 + selected
            outputs = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                logits_to_keep=positions,
                use_cache=False,
            )
            # Checkpoint the vocabulary reduction in bounded chunks. Keeping
            # float32 log-softmax/entropy graphs for 4096 x 248k tokens would
            # exhaust the authorized single card without changing any targets.
            from torch.utils.checkpoint import checkpoint

            def reduce(logits, targets):
                values = logits.float() / self.temperature
                log_probs = torch.log_softmax(values, -1)
                chosen = log_probs.gather(-1, targets[..., None]).squeeze(-1)
                # Entropy is diagnostic only (no entropy bonus in this recipe).
                with torch.no_grad():
                    entropy = -(log_probs.exp() * log_probs).sum(-1)
                return chosen, entropy

            chosen, entropy = [], []
            targets = input_ids[:, -logits_to_keep:][:, selected]
            for start in range(0, len(selected), 128):
                args = (
                    outputs.logits[:, start : start + 128],
                    targets[:, start : start + 128],
                )
                lp, ent = (
                    checkpoint(reduce, *args, use_reentrant=False)
                    if torch.is_grad_enabled()
                    else reduce(*args)
                )
                chosen.append(lp)
                entropy.append(ent)
            chosen, entropy = torch.cat(chosen, 1), torch.cat(entropy, 1)
            if mask is not None:
                # Only scalar logps are scattered; no vocabulary logits for tools.
                indices = selected.unsqueeze(0)
                shape = (1, logits_to_keep)
                chosen = chosen.new_zeros(shape).scatter(1, indices, chosen)
                entropy = entropy.new_zeros(shape).scatter(1, indices, entropy)
            self.recoveragent_last_logps = chosen.detach()
            return chosen, entropy, None

    return AppWorldGRPO


def make_trainer(model, tokenizer, output, settings):
    from datasets import Dataset
    from trl import GRPOConfig

    args = GRPOConfig(
        output_dir=str(output),
        learning_rate=settings["learning_rate"],
        per_device_train_batch_size=1,
        gradient_accumulation_steps=1,
        generation_batch_size=4,
        num_generations=4,
        beta=0.0,
        epsilon=settings.get("clip_low", 0.2),
        epsilon_high=settings.get("clip_high", 0.2),
        max_completion_length=4096,
        temperature=settings["temperature"],
        top_p=1.0,
        top_k=0,
        loss_type="grpo",
        bf16=next(model.parameters()).is_cuda,
        report_to=[],
        save_strategy="no",
        optim="adamw_torch",
        seed=20261109,
    )
    trainer = trainer_class()(
        model=model,
        args=args,
        processing_class=getattr(tokenizer, "tokenizer", tokenizer),
        train_dataset=Dataset.from_dict(
            {"prompt": ["Environment-managed rollout"] * 4}
        ),
        reward_funcs=lambda completions, **kwargs: [None] * len(completions),
    )
    trainer.current_gradient_accumulation_steps = 1
    trainer.recoveragent_activation_offload = settings.get("activation_offload")
    trainer.recoveragent_coalesce = settings.get("coalesce_exact_prefixes", False)
    # Reference probabilities are supplied explicitly using a second frozen
    # adapter on the same base; do not allocate a second 9B model.
    trainer.beta = settings.get("beta", 0.0)
    trainer.create_optimizer()
    return trainer


def update_batch(trainer, groups, *, max_length=None):
    import torch

    usable = [(episodes, group_advantages(episodes)) for episodes in groups]
    usable = [(e, a) for e, a in usable if a is not None]
    if not usable or not any(any(x != 0 for x in a) for _, a in usable):
        return {"updated": False, "reason": "no_scorable_reward_variation"}
    model = trainer.model
    model.train()
    trainer.optimizer.zero_grad()
    total_loss = 0.0
    ratio_sum, ratio_count, outside, clipped = 0.0, 0, 0, 0
    work = {
        "request_segments": 0,
        "training_segments": 0,
        "original_sequence_tokens": 0,
        "trained_sequence_tokens": 0,
        "cpu_offloaded_segments": 0,
    }
    chain_audit = []
    for episodes, advantages in usable:
        for episode, advantage in zip(episodes, advantages, strict=True):
            if advantage == 0 and trainer.beta == 0:
                continue
            rows = segments(
                episode["directory"], episode["policy"], max_length=max_length
            )
            tokens = sum(len(r["completion_ids"]) for r in rows)
            work["request_segments"] += len(rows)
            work["original_sequence_tokens"] += sum(
                len(r["prompt_ids"]) + len(r["completion_ids"]) for r in rows
            )
            if trainer.recoveragent_coalesce:
                rows = coalesce(rows)
                chain_audit.extend(
                    {
                        "episode": episode["directory"],
                        "requests": r["request_refs"],
                        "sequence_tokens": len(r["prompt_ids"])
                        + len(r["completion_ids"]),
                        "target_tokens": sum(r["tool_mask"]),
                    }
                    for r in rows
                )
            for row in rows:
                sequence_tokens = len(row["prompt_ids"]) + len(row["completion_ids"])
                work["training_segments"] += 1
                work["trained_sequence_tokens"] += sequence_tokens
                mask = row.get("tool_mask", [1] * len(row["completion_ids"]))
                target_tokens = sum(mask)

                def tensor(value, dtype=torch.long):
                    return torch.tensor([value], device=model.device, dtype=dtype)

                inputs = {
                    "prompt_ids": tensor(row["prompt_ids"]),
                    "prompt_mask": tensor([1] * len(row["prompt_ids"])),
                    "completion_ids": tensor(row["completion_ids"]),
                    "completion_mask": tensor([1] * len(row["completion_ids"])),
                    "old_per_token_logps": tensor(row["logprobs"], torch.float32),
                    "advantages": torch.tensor([advantage], device=model.device),
                }
                if "tool_mask" in row:
                    inputs["tool_mask"] = tensor(mask)
                    inputs["sampling_mask"] = tensor(mask)
                if trainer.beta:
                    model.set_adapter("coevolve_reference", inference_mode=True)
                    try:
                        with torch.no_grad():
                            inputs["ref_per_token_logps"], _, _ = (
                                trainer._get_per_token_logps_and_entropies(
                                    model,
                                    torch.cat(
                                        [
                                            inputs["prompt_ids"],
                                            inputs["completion_ids"],
                                        ],
                                        1,
                                    ),
                                    torch.cat(
                                        [
                                            inputs["prompt_mask"],
                                            inputs["completion_mask"],
                                        ],
                                        1,
                                    ),
                                    len(row["completion_ids"]),
                                    sampling_mask=inputs.get("sampling_mask"),
                                )
                            )
                    finally:
                        model.set_adapter("default")
                offload = offload_row(
                    trainer.recoveragent_activation_offload, sequence_tokens
                )
                work["cpu_offloaded_segments"] += int(offload)
                context = (
                    torch.autograd.graph.save_on_cpu(pin_memory=True)
                    if offload
                    else nullcontext()
                )
                with context:
                    loss = trainer.compute_loss(model, inputs)
                current = getattr(trainer, "recoveragent_last_logps", None)
                if current is not None:
                    with torch.no_grad():
                        ratio = (current - inputs["old_per_token_logps"]).exp()[
                            tensor(mask).bool()
                        ]
                        low, high = 1 - trainer.epsilon_low, 1 + trainer.epsilon_high
                        ratio_sum += float(ratio.sum())
                        ratio_count += ratio.numel()
                        outside += int(((ratio < low) | (ratio > high)).sum())
                        clipped += int(
                            ((ratio > high) if advantage > 0 else (ratio < low)).sum()
                        )
                loss = loss * (target_tokens / tokens) / (4 * len(usable))
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite GRPO loss")
                trainer.accelerator.backward(loss)
                total_loss += float(loss.detach())
    health = gradient_health(model)
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
    trainer.optimizer.step()
    trainer.state.global_step += 1
    return {
        "updated": True,
        "loss": total_loss,
        "probability_ratio_mean": ratio_sum / ratio_count if ratio_count else None,
        "ratio_outside_clip_fraction": outside / ratio_count if ratio_count else None,
        "effective_clip_fraction": clipped / ratio_count if ratio_count else None,
        "ratio_token_count": ratio_count,
        "sequence_work": work,
        "coalesced_chains": chain_audit,
        **health,
        "contributing_generated_task_ids": sorted(
            {
                e["generated_task_id"]
                for episodes, advantages in usable
                for e, a in zip(episodes, advantages, strict=True)
                if a != 0 and e["generated_task_id"] is not None
            }
        ),
    }


def train(
    config,
    identity,
    parent,
    output,
    tokenizer,
    schedule,
    rollout,
    deadline,
    *,
    remaining_tokens=None,
    task_at=None,
    after_batch=None,
):
    import torch
    from peft import get_peft_model_state_dict

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    model = load_trainable(identity, config["lora"], parent)
    model.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    before = tensor_hash(get_peft_model_state_dict(model))
    reference_hash = None
    if config["grpo"].get("beta", 0):
        model.load_adapter(
            str(parent), adapter_name="coevolve_reference", is_trainable=False
        )
        model.set_adapter("default")
        reference_hash = tensor_hash(
            get_peft_model_state_dict(model, adapter_name="coevolve_reference")
        )
        if reference_hash != before:
            raise ValueError("Frozen GRPO reference differs from S1")
    trainer = make_trainer(model, tokenizer, output / "trainer", config["grpo"])
    started, audit, probe_ids = time.monotonic(), [], None
    torch.cuda.reset_peak_memory_stats()
    for batch_index in range(0, len(schedule), 2):
        if time.monotonic() >= deadline:
            raise TimeoutError("Shared GPU job deadline reached")
        groups, checkpoint_id = [], policy_id(model)
        for i, task in enumerate(schedule[batch_index : batch_index + 2]):
            if task_at is not None:
                task = task_at(batch_index + i, task)
                if task is None:
                    continue
            if (
                remaining_tokens is not None
                and remaining_tokens()
                < 4 * config["student_limits"]["max_generated_tokens"]
            ):
                # Reserve one complete group before its first trajectory. A
                # partial final update may contain one complete four-way group.
                break
            group = [
                rollout(model, checkpoint_id, task, batch_index + i, member)
                for member in range(4)
            ]
            groups.append(group)
        if not groups:
            break
        update = update_batch(trainer, groups, max_length=sequence_limit(config))
        audit.append(
            {
                "batch": batch_index // 2,
                "policy_id": checkpoint_id,
                "groups": groups,
                "update": update,
                "reward_varying_groups": sum(
                    a is not None and any(a)
                    for a in (group_advantages(g) for g in groups)
                ),
            }
        )
        write_json(output / "updates" / f"{batch_index // 2:03d}.json", audit[-1])
        if probe_ids is None:
            for group in groups:
                for episode in group:
                    if episode["classification"] != "infrastructure_error":
                        probe_ids = segments(
                            episode["directory"],
                            episode["policy"],
                            max_length=sequence_limit(config),
                        )[0]["prompt_ids"][:128]
                        break
        # Save updated adapters as real private intermediate states, never replay old rollouts.
        if update["updated"]:
            model.save_pretrained(
                output / f"update_{trainer.state.global_step:02d}",
                selected_adapters=["default"],
            )
        if after_batch is not None and batch_index + 2 < len(schedule):
            after_batch(batch_index // 2, groups)
    write_json(output / "updates.json", audit)
    if trainer.state.global_step == 0:
        write_json(
            output / "no_update.json",
            {
                "reason": "All groups unscorable or constant reward",
                "optimizer_steps": 0,
            },
        )
        del trainer, model
        gc.collect()
        torch.cuda.empty_cache()
        raise NoPolicyUpdate("GRPO produced no update; S2 is not a trained checkpoint")
    metadata = {
        "stage": "S2_grpo",
        "optimizer_steps": trainer.state.global_step,
        "groups": sum(len(item["groups"]) for item in audit),
        "scheduled_groups": len(schedule),
        "wall_seconds": time.monotonic() - started,
        "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "reference_adapter_sha256": reference_hash,
        "beta": trainer.beta,
    }
    if reference_hash:
        if (
            tensor_hash(
                get_peft_model_state_dict(model, adapter_name="coevolve_reference")
            )
            != reference_hash
        ):
            raise ValueError("GRPO reference adapter changed")
        model.delete_adapter("coevolve_reference")
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
    torch.save(trainer.optimizer.state_dict(), output / "optimizer.pt")
    del trainer, model
    gc.collect()
    torch.cuda.empty_cache()
    return finalize_reload(
        output, identity, before, after, probe, probe_ids, parent, metadata
    )
