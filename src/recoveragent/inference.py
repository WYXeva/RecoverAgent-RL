"""One local Student shared by sequential environment processes; inference only."""

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(4)
    tokenizer = AutoTokenizer.from_pretrained(
        args.model, revision=args.revision, local_files_only=True
    )
    model = (
        AutoModelForCausalLM.from_pretrained(
            args.model,
            revision=args.revision,
            torch_dtype=torch.bfloat16,
            local_files_only=True,
            attn_implementation="sdpa",
        )
        .to(args.device)
        .eval()
    )
    identity = {
        "model": args.model,
        "revision": args.revision,
        "thinking": False,
        "backend": "transformers",
        "dtype": "bfloat16",
        "chat_template_hash": hashlib.sha256(
            tokenizer.chat_template.encode()
        ).hexdigest(),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(identity)

        def respond(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            try:
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                inputs = tokenizer.apply_chat_template(
                    value["messages"],
                    tools=value["tools"],
                    tokenize=True,
                    add_generation_prompt=True,
                    enable_thinking=False,
                    return_tensors="pt",
                    return_dict=True,
                ).to(args.device)
                prompt_tokens = inputs["input_ids"].shape[1]
                if prompt_tokens > value["max_input_tokens"]:
                    self.respond(
                        {
                            "error": "context_budget_exhausted",
                            "prompt_tokens": prompt_tokens,
                        }
                    )
                    return
                torch.manual_seed(value["seed"])
                with torch.inference_mode():
                    output = model.generate(
                        **inputs,
                        max_new_tokens=value["max_new_tokens"],
                        do_sample=True,
                        temperature=value["temperature"],
                        top_p=value["top_p"],
                        pad_token_id=tokenizer.eos_token_id,
                    )
                tokens = output[0, prompt_tokens:].tolist()
                self.respond(
                    {
                        "text": tokenizer.decode(tokens, skip_special_tokens=True),
                        "token_ids": tokens,
                        "input_token_ids": inputs["input_ids"][0].tolist(),
                        "prompt_tokens": prompt_tokens,
                        "generated_tokens": len(tokens),
                        "policy": identity,
                    }
                )
            except Exception as exc:
                self.respond({"error": type(exc).__name__ + ": " + str(exc)})

        def log_message(self, *args):
            pass  # Do not dump task content or headers to service logs.

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(json.dumps({"ready": True, "policy": identity}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
