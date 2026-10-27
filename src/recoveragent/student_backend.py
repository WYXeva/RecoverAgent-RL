"""Private single-GPU Qwen3-4B generation backend for paired P3-B evaluation."""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from .base_sft import MODEL, REVISION, TEMPLATE_SHA256, load_tokenizer
from .io import read_json


def validate_request(request, identity, tokenizer):
    if request["policy"] != identity:
        raise ValueError("Loaded checkpoint differs from requested policy")
    text = tokenizer.apply_chat_template(
        request["messages"],
        tools=request["tools"],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    ids = tokenizer(text, add_special_tokens=False)["input_ids"]
    limit = request["max_new_tokens"]
    if request["input_token_ids"] != ids:
        raise ValueError("Actual model input differs from session receipt")
    if type(limit) is not int or not 1 <= limit <= 4096 or len(ids) + limit > 32768:
        raise ValueError("Invalid full-context generation budget")
    return ids


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--adapter")
    p.add_argument("--port", type=int, default=8766)
    a = p.parse_args()
    import torch
    from transformers import AutoModelForCausalLM
    from peft import PeftModel, get_peft_model_state_dict
    from .sft_recipe import tensor_hash

    torch.set_num_threads(4)
    tokenizer = load_tokenizer()
    model = AutoModelForCausalLM.from_pretrained(
        MODEL,
        revision=REVISION,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    checkpoint_id = "raw_base:" + REVISION
    if a.adapter:
        metadata = read_json(a.adapter + "/recoveragent_checkpoint.json")
        if (
            not metadata["parameter_update_and_reload_pass"]
            or metadata["model"] != MODEL
            or metadata["revision"] != REVISION
            or metadata.get("runtime_contract") != "bf16_autocast_reload_v1"
        ):
            raise ValueError("Unverified adapter checkpoint")
        model = PeftModel.from_pretrained(model, a.adapter, is_trainable=False)
        actual_hash = tensor_hash(get_peft_model_state_dict(model))
        if actual_hash != metadata["trainable_after_hash"]:
            raise ValueError("Adapter parameters changed since training verification")
        checkpoint_id = "adapter:" + actual_hash
    model.to("cuda").eval()
    identity = {
        "model": MODEL,
        "revision": REVISION,
        "thinking": False,
        "chat_template_sha256": TEMPLATE_SHA256,
        "checkpoint_id": checkpoint_id,
    }

    class Handler(BaseHTTPRequestHandler):
        def respond(self, value, status=200):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.respond(identity)

        def do_POST(self):
            try:
                size = int(self.headers["Content-Length"])
                if not 1 <= size <= 4 * 1024 * 1024:
                    raise ValueError("Invalid request size")
                request = json.loads(self.rfile.read(size))
                ids = validate_request(request, identity, tokenizer)
                inputs = torch.tensor([ids], dtype=torch.long, device="cuda")
                torch.manual_seed(request["seed"])
                torch.cuda.reset_peak_memory_stats()
                started = time.monotonic()
                with (
                    torch.inference_mode(),
                    torch.autocast("cuda", dtype=torch.bfloat16),
                ):
                    out = model.generate(
                        input_ids=inputs,
                        attention_mask=torch.ones_like(inputs),
                        max_new_tokens=request["max_new_tokens"],
                        do_sample=True,
                        temperature=request["temperature"],
                        top_p=request["top_p"],
                        pad_token_id=tokenizer.pad_token_id,
                        eos_token_id=tokenizer.eos_token_id,
                        use_cache=True,
                    )
                tokens = out[0, len(ids) :].tolist()
                self.respond(
                    {
                        "policy": identity,
                        "input_token_ids": ids,
                        "token_ids": tokens,
                        **(
                            {
                                "text": tokenizer.decode(
                                    tokens, skip_special_tokens=False
                                )
                            }
                            if request.get("purpose") == "memory_update"
                            else {}
                        ),
                        "finish_reason": "eos"
                        if tokens and tokens[-1] == tokenizer.eos_token_id
                        else "length",
                        "generation_seconds": time.monotonic() - started,
                        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                    }
                )
            except Exception as exc:
                self.respond({"error_type": type(exc).__name__}, status=500)

        def log_message(self, *args):
            pass

    print(json.dumps({"status": "READY", "policy": identity}), flush=True)
    HTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
