"""Real PEFT merge check on a tiny random CPU Qwen3; not M1/GPU acceptance."""

import argparse
import json
import tempfile
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import Qwen3Config, Qwen3ForCausalLM

from recoveragent.io import files_manifest, write_json
from recoveragent.m1_inference_probe import merge_in_memory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    torch.set_num_threads(1)
    rows = []
    for dtype in (torch.float32, torch.bfloat16):
        torch.manual_seed(42)
        config = Qwen3Config(
            vocab_size=128,
            hidden_size=32,
            intermediate_size=64,
            num_hidden_layers=2,
            num_attention_heads=4,
            num_key_value_heads=2,
            head_dim=8,
            max_position_embeddings=128,
        )
        base = Qwen3ForCausalLM(config).to(device="cpu", dtype=dtype)
        model = get_peft_model(
            base,
            LoraConfig(
                r=16,
                lora_alpha=32,
                lora_dropout=0.05,
                task_type="CAUSAL_LM",
                target_modules=[
                    "q_proj",
                    "k_proj",
                    "v_proj",
                    "o_proj",
                    "gate_proj",
                    "up_proj",
                    "down_proj",
                ],
            ),
        )
        # Default LoRA B is zero: deliberately use a nonzero adapter so the
        # check cannot pass just because the merge is a no-op.
        with torch.no_grad():
            for name, parameter in model.named_parameters():
                if "lora_B" in name:
                    parameter.normal_(mean=0, std=0.01)
        model.eval()
        inputs = torch.randint(0, config.vocab_size, (1, 32))
        with torch.inference_mode():
            before = model(input_ids=inputs).logits.float()
            with model.disable_adapter():
                raw = model(input_ids=inputs).logits.float()
        assert not torch.equal(before, raw), "Adapter did not affect the model"
        with tempfile.TemporaryDirectory(prefix="m1-merge-cpu-") as tmp:
            model.save_pretrained(tmp)
            original_files = files_manifest(tmp)
            merged = merge_in_memory(model)
            with torch.inference_mode():
                after = merged(input_ids=inputs).logits.float()
            assert files_manifest(tmp) == original_files
        assert torch.isfinite(after).all()
        error = (before - after).abs().max().item()
        tolerance = 1e-5 if dtype == torch.float32 else 0.02
        assert error <= tolerance, (dtype, error)
        rows.append(
            {
                "dtype": str(dtype),
                "max_abs_logit_error": error,
                "tolerance": tolerance,
                "adapter_effect_nonzero": True,
                "original_adapter_files_unchanged": True,
            }
        )
    assert not torch.cuda.is_initialized()
    receipt = {
        "status": "PASS",
        "scope": "tiny_random_Qwen3_CPU_only",
        "actual_M1_tested": False,
        "GPU_speedup_measured": False,
        "CUDA_initialized": False,
        "rows": rows,
    }
    write_json(output, receipt)
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
