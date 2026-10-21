"""CPU synthetic Qwen3 architecture check; no pretrained weights or optimizer."""

import argparse
import torch
import torch.nn.functional as F
from transformers import Qwen3Config, Qwen3ForCausalLM

from recoveragent.io import write_json
from recoveragent.sft_recipe import collate, selected_token_loss, gradient_health


def probe():
    torch.manual_seed(42)
    config = Qwen3Config(
        vocab_size=32,
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=1,
        num_attention_heads=4,
        num_key_value_heads=2,
        head_dim=8,
        attention_dropout=0.0,
        tie_word_embeddings=False,
    )
    model = Qwen3ForCausalLM(config).eval()
    row = {
        "input_ids": [1, 2, 3, 4, 5, 6],
        "attention_mask": [1] * 6,
        "labels": [-100, -100, -100, 4, 5, 6],
        "example_weight": 2.5,
    }
    batch = collate([row])
    selected, output = selected_token_loss(model, batch)
    selected.backward()
    health = gradient_health(model)
    selected_grad = model.lm_head.weight.grad.detach().clone()
    model.zero_grad()
    full = model(input_ids=batch["input_ids"], use_cache=False).logits
    reference = (
        F.cross_entropy(
            full[:, :-1].reshape(-1, 32), batch["labels"][:, 1:].reshape(-1)
        )
        * 2.5
    )
    reference.backward()
    assert output.logits.shape == (1, 3, 32)
    assert torch.allclose(selected, reference, atol=1e-6)
    assert torch.allclose(selected_grad, model.lm_head.weight.grad, atol=1e-6)
    light, _ = selected_token_loss(model, collate([{**row, "example_weight": 1.0}]))
    assert torch.allclose(selected, light * 2.5, atol=1e-6)
    return {
        "status": "PASS",
        "synthetic_architecture_only": True,
        "real_transformers_qwen3_forward": True,
        "full_ce_equivalent": True,
        "gradient_equivalent": True,
        "weighted_sequence_mean_verified": True,
        "synthetic_gradient_health": health,
        "supervised_positions": 3,
        "masked_positions": 3,
        "logit_positions_computed": output.logits.shape[1],
        "pretrained_weights_loaded": False,
        "optimizer_steps": 0,
        "student_training_started": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    result = probe()
    write_json(a.output, result)
    print(result)
