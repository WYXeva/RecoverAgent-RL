"""Current-policy AppWorld groups, using TRL's clipped GRPO loss and optimizer.

Dynamic discovery changes the next request's tool catalogue. Consequently a
multi-turn episode is NOT one append-only token sequence. Each actual request
is scored separately, with the same episode advantage, and token-weighted
within its episode. Tools/context never enter the completion loss.
"""

from contextlib import nullcontext
import gc
import math
import time
from pathlib import Path

from .io import digest, read_json, write_json
from .sft_recipe import gradient_health, tensor_hash
from .v2_inference import policy_id
from .v2_training import load_trainable, finish_checkpoint, finalize_reload


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
    if any(
        e["classification"] == "infrastructure_error" or type(e["success"]) is not bool
        for e in episodes
    ):
        return None
    rewards = [float(e["success"]) for e in episodes]
    mean = sum(rewards) / 4
    std = math.sqrt(sum((r - mean) ** 2 for r in rewards) / 3)
    return [(r - mean) / (std + 1e-4) for r in rewards]


def segments(directory, expected_policy):
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
        if len(request["input_token_ids"]) + len(response["token_ids"]) > 32768:
            raise ValueError("GRPO segment exceeds context; no truncation")
        rows.append(
            {
                "prompt_ids": request["input_token_ids"],
                "completion_ids": response["token_ids"],
                "logprobs": response["logprobs"],
                "request_sha256": digest(request),
                "response_sha256": digest(response),
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
            positions = torch.arange(
                input_ids.shape[1] - logits_to_keep - 1,
                input_ids.shape[1] - 1,
                device=input_ids.device,
            )
            outputs = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                logits_to_keep=positions,
                use_cache=False,
            )
            logits = outputs.logits.float() / self.temperature
            log_probs = torch.log_softmax(logits, -1)
            chosen = log_probs.gather(-1, input_ids[:, -logits_to_keep:, None]).squeeze(
                -1
            )
            entropy = -(log_probs.exp() * log_probs).sum(-1)
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
    trainer.create_optimizer()
    return trainer


def update_batch(trainer, groups):
    import torch

    usable = [(episodes, group_advantages(episodes)) for episodes in groups]
    usable = [(e, a) for e, a in usable if a is not None]
    if not usable or not any(any(x != 0 for x in a) for _, a in usable):
        return {"updated": False, "reason": "no_scorable_reward_variation"}
    model = trainer.model
    model.train()
    trainer.optimizer.zero_grad()
    total_loss = 0.0
    for episodes, advantages in usable:
        for episode, advantage in zip(episodes, advantages, strict=True):
            if advantage == 0:
                continue
            rows = segments(episode["directory"], episode["policy"])
            tokens = sum(len(r["completion_ids"]) for r in rows)
            for row in rows:

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
                context = (
                    torch.autograd.graph.save_on_cpu(pin_memory=True)
                    if trainer.recoveragent_activation_offload == "cpu"
                    else nullcontext()
                )
                with context:
                    loss = trainer.compute_loss(model, inputs)
                loss = loss * (len(row["completion_ids"]) / tokens) / (4 * len(usable))
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
    trainer = make_trainer(model, tokenizer, output / "trainer", config["grpo"])
    started, audit, probe_ids = time.monotonic(), [], None
    torch.cuda.reset_peak_memory_stats()
    for batch_index in range(0, len(schedule), 2):
        if time.monotonic() >= deadline:
            raise TimeoutError("Shared GPU job deadline reached")
        groups, checkpoint_id = [], policy_id(model)
        for i, task in enumerate(schedule[batch_index : batch_index + 2]):
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
        update = update_batch(trainer, groups)
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
                        probe_ids = segments(episode["directory"], episode["policy"])[
                            0
                        ]["prompt_ids"][:128]
                        break
        # Save updated adapters as real private intermediate states, never replay old rollouts.
        if update["updated"]:
            model.save_pretrained(output / f"update_{trainer.state.global_step:02d}")
    write_json(output / "updates.json", audit)
    if trainer.state.global_step == 0:
        write_json(
            output / "no_update.json",
            {
                "reason": "All groups unscorable or constant reward",
                "optimizer_steps": 0,
            },
        )
        raise ValueError("GRPO produced no update; S2 is not a trained checkpoint")
    metadata = {
        "stage": "S2_grpo",
        "optimizer_steps": trainer.state.global_step,
        "groups": sum(len(item["groups"]) for item in audit),
        "scheduled_groups": len(schedule),
        "wall_seconds": time.monotonic() - started,
        "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
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
    torch.save(trainer.optimizer.state_dict(), output / "optimizer.pt")
    del trainer, model
    gc.collect()
    torch.cuda.empty_cache()
    return finalize_reload(
        output, identity, before, after, probe, probe_ids, parent, metadata
    )
