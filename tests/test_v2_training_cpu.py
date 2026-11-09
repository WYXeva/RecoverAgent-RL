"""Optional tiny random-weight CPU tests. These are NOT pretrained-model runs."""

import copy

import pytest

from recoveragent import v2_training as runtime
from recoveragent.io import read_json
from recoveragent.sft_recipe import selected_token_loss, tensor_hash, gradient_health
from recoveragent.v2_grpo import make_trainer


torch = pytest.importorskip("torch")
peft = pytest.importorskip("peft")
pytest.importorskip("trl")
transformers = pytest.importorskip("transformers")
if not hasattr(transformers, "Qwen3_5ForConditionalGeneration"):
    pytest.skip("Run in the pinned v2 environment", allow_module_level=True)


def tiny():
    torch.manual_seed(42)
    text = transformers.Qwen3_5TextConfig(
        vocab_size=128,
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        head_dim=8,
        linear_num_value_heads=4,
        linear_num_key_heads=2,
        linear_key_head_dim=8,
        linear_value_head_dim=8,
        layer_types=["linear_attention", "full_attention"],
        max_position_embeddings=128,
    )
    config = transformers.Qwen3_5Config(
        text_config=text.to_dict(),
        vision_config={
            "depth": 1,
            "hidden_size": 32,
            "intermediate_size": 64,
            "num_heads": 4,
            "out_hidden_size": 32,
            "patch_size": 2,
            "spatial_merge_size": 1,
            "temporal_patch_size": 2,
        },
    )
    return transformers.Qwen3_5ForConditionalGeneration(config)


def tokenizer():
    from tokenizers import Tokenizer, models

    raw = Tokenizer(models.WordLevel({str(i): i for i in range(128)}, unk_token="0"))
    return transformers.PreTrainedTokenizerFast(
        tokenizer_object=raw, pad_token="0", eos_token="1", chat_template="{{messages}}"
    )


def inputs():
    ids = torch.tensor([[3, 4, 5, 6, 7, 8]])
    labels = ids.clone()
    labels[:, :3] = -100
    return {
        "input_ids": ids,
        "labels": labels,
        "attention_mask": torch.ones_like(ids),
        "example_weight": torch.ones(1),
    }


def test_s0_reload_s1_loads_trainable_parent_and_updates(tmp_path, monkeypatch):
    cfg = read_json("configs/v2_round1_20261109.json")
    identity = cfg["models"]["primary"]
    monkeypatch.setattr(runtime, "load_base", lambda *a, **kw: tiny())
    model = runtime.load_trainable(identity, {**cfg["lora"], "r": 2})
    before = tensor_hash(peft.get_peft_model_state_dict(model))
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=0.01
    )
    loss, _ = selected_token_loss(model, inputs())
    loss.backward()
    assert gradient_health(model)["l2_norm"] > 0
    optimizer.step()
    output = tmp_path / "S0_tiny_random_test"
    output.mkdir()
    after, probe = runtime.finish_checkpoint(
        model,
        tokenizer(),
        output,
        identity,
        before,
        parent=None,
        metadata={},
        probe_ids=[3, 4, 5],
    )
    runtime.finalize_reload(
        output,
        identity,
        before,
        after,
        probe,
        [3, 4, 5],
        None,
        {"synthetic_random_model": True},
    )
    continued = runtime.load_trainable(identity, cfg["lora"], output)
    assert tensor_hash(peft.get_peft_model_state_dict(continued)) == after != before
    assert any(p.requires_grad for p in continued.parameters())
    optimizer = torch.optim.AdamW(
        [p for p in continued.parameters() if p.requires_grad], lr=0.01
    )
    loss, _ = selected_token_loss(continued, inputs())
    loss.backward()
    gradient_health(continued)
    optimizer.step()
    assert tensor_hash(peft.get_peft_model_state_dict(continued)) != after
    with pytest.raises(ValueError):
        runtime.verify_checkpoint(output, cfg["models"]["fallback"])


def test_trl_grpo_loss_updates_only_current_adapter(tmp_path):
    cfg = read_json("configs/v2_round1_20261109.json")
    model = peft.get_peft_model(
        tiny(),
        peft.LoraConfig(
            r=2,
            lora_alpha=4,
            target_modules=r".*language_model\..*\.(q_proj|v_proj)",
            task_type="CAUSAL_LM",
        ),
    )
    before = tensor_hash(peft.get_peft_model_state_dict(model))
    trainer = make_trainer(model, tokenizer(), tmp_path, cfg["grpo"])
    model.train()
    data = inputs()
    with torch.no_grad():
        old, _, _ = trainer._get_per_token_logps_and_entropies(
            model, data["input_ids"], data["attention_mask"], 3
        )
    batch = {
        "prompt_ids": data["input_ids"][:, :3],
        "prompt_mask": torch.ones((1, 3), dtype=torch.long),
        "completion_ids": data["input_ids"][:, 3:],
        "completion_mask": torch.ones((1, 3), dtype=torch.long),
        "old_per_token_logps": old,
        "advantages": torch.tensor([1.0]),
    }
    loss = trainer.compute_loss(model, copy.deepcopy(batch))
    loss.backward()
    assert gradient_health(model)["l2_norm"] > 0
    trainer.optimizer.step()
    assert tensor_hash(peft.get_peft_model_state_dict(model)) != before


def test_coevolve_frozen_s1_reference_and_chunked_scores(tmp_path):
    """Actual CPU gradients through TRL, not pretrained-model evidence."""
    cfg = read_json("configs/v2_round1_coevolve_20261109.json")
    model = peft.get_peft_model(
        tiny(),
        peft.LoraConfig(
            r=2,
            lora_alpha=4,
            target_modules=r".*language_model\..*\.(q_proj|v_proj)",
            task_type="CAUSAL_LM",
        ),
    )
    parent = tmp_path / "reference"
    model.save_pretrained(parent)
    model.load_adapter(parent, adapter_name="coevolve_reference", is_trainable=False)
    model.set_adapter("default")
    before = tensor_hash(
        peft.get_peft_model_state_dict(model, adapter_name="coevolve_reference")
    )
    trainer = make_trainer(model, tokenizer(), tmp_path / "trainer", cfg["grpo"])
    ids = torch.arange(140).remainder(125).add(2).unsqueeze(0)
    mask = torch.ones_like(ids)
    model.eval()
    with torch.no_grad():
        selected, entropy, _ = trainer._get_per_token_logps_and_entropies(
            model, ids, mask, 130
        )
        logits = (
            model(
                input_ids=ids,
                attention_mask=mask,
                logits_to_keep=torch.arange(9, 139),
                use_cache=False,
            ).logits.float()
            / trainer.temperature
        )
        full = torch.log_softmax(logits, -1)
        expected = full.gather(-1, ids[:, -130:, None]).squeeze(-1)
        assert torch.allclose(selected, expected, atol=1e-6)
        assert torch.allclose(entropy, -(full.exp() * full).sum(-1), atol=1e-6)
    model.set_adapter("coevolve_reference", inference_mode=True)
    with torch.no_grad():
        ref, _, _ = trainer._get_per_token_logps_and_entropies(model, ids, mask, 130)
    model.set_adapter("default")
    model.train()
    batch = {
        "prompt_ids": ids[:, :10],
        "prompt_mask": mask[:, :10],
        "completion_ids": ids[:, 10:],
        "completion_mask": mask[:, 10:],
        "old_per_token_logps": selected,
        "ref_per_token_logps": ref,
        "advantages": torch.tensor([1.0]),
    }
    loss = trainer.compute_loss(model, batch)
    loss.backward()
    assert gradient_health(model)["l2_norm"] > 0
    assert all(
        p.grad is None for n, p in model.named_parameters() if "coevolve_reference" in n
    )
    trainer.optimizer.step()
    assert (
        tensor_hash(
            peft.get_peft_model_state_dict(model, adapter_name="coevolve_reference")
        )
        == before
    )
    assert tensor_hash(peft.get_peft_model_state_dict(model)) != before
