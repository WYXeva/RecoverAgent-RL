"""Original unmerged M1 serving after the independent merge candidate failed.

The sealed 20261030 engines are never modified. A deadline response is audited,
its tokens counted, and no partial tool call is executed. Other transport/backend
errors remain infrastructure failures. No automatic retries or model selection.
"""

import argparse
import json
import math
from pathlib import Path
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import full732_c244 as original
from .base_sft import MODEL, REVISION, TEMPLATE_SHA256, load_tokenizer
from .demonstrations import sha256
from .full732_m1_backend import validate_request
from .io import read_json, write_json
from . import m1_inference_probe as probe
from . import m1_vgpu_runtime_v2 as status_runtime
from .student_evaluation import execution_clock, request_json

ROOT = original.ROOT
LOCK = ROOT / "configs/m1_vgpu_reference_20261030_lock.json"
VARIANT = "m1_vgpu32_reference_lora_deadline_20261030"


def verify_lock():
    status_runtime.verify_lock()
    lock = read_json(LOCK)
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("VGPU source changed: " + name)
    return sha256(LOCK)


def remaining(deadline, now):
    if not isinstance(deadline, (float, int)) or isinstance(deadline, bool):
        raise ValueError("Missing monotonic session deadline")
    value = deadline - now
    if not math.isfinite(value) or value > 600:
        raise ValueError("Invalid session deadline")
    return max(0.0, value)


class GenerationDeadline(Exception):
    """Server-confirmed generation stop at the original session deadline."""


def validate_deadline_response(response, request):
    ids = response.get("token_ids")
    if (
        response.get("policy") != request["policy"]
        or response.get("input_token_ids") != request["input_token_ids"]
        or response.get("finish_reason") != "deadline"
        or response.get("runtime_variant") != VARIANT
        or response.get("deadline_monotonic") != request["deadline_monotonic"]
        or not isinstance(ids, list)
        or any(type(i) is not int or i < 0 for i in ids)
        or len(ids) > request["max_new_tokens"]
    ):
        raise ValueError("Invalid deadline receipt; treat as infrastructure error")
    return ids


def validate_probe(receipt, parameter_hash):
    """Accept only the completed original PEFT memory probe, not the failed merge."""
    plan = read_json(probe.PLAN)
    if (
        receipt.get("plan") != plan
        or parameter_hash != plan["adapter_parameter_hash"]
        or receipt.get("policy", {}).get("checkpoint_id") != "adapter:" + parameter_hash
        or receipt.get("source_lock_sha256") != status_runtime.verify_lock()
        or receipt.get("synthetic_only") is not True
        or receipt.get("training") is not False
        or receipt.get("C244_started") is not False
    ):
        raise ValueError("Reference memory probe identity mismatch")
    groups = receipt.get("measurements", {}).get("reference", [])
    if len(groups) != 3:
        raise ValueError("Missing reference context measurements")
    for length, rows in zip((2073, 16409, 32633), groups, strict=True):
        if len(rows) != plan["repeats"]:
            raise ValueError("Incomplete reference repeats")
        for row in rows:
            if (
                row["input_tokens"] != length
                or row["output_tokens"] != 128
                or len(row["token_ids"]) != 128
                or any(type(i) is not int or i < 0 for i in row["token_ids"])
                or length + row["output_tokens"] > 32768
                or not math.isfinite(row["generation_seconds"])
                or row["generation_seconds"] <= 0
                or not 0
                < row["peak_allocated_bytes"]
                <= row["peak_reserved_bytes"]
                < 32760 * 1024**2
            ):
                raise ValueError("Invalid reference generation or memory evidence")


def deadline_engine(url):
    runner = original.engine()
    parent = runner.StudentSession

    class Session(parent):
        def run(self, unused_generate):
            def generate(request):
                deadline = self.started + self.wall_seconds
                now = execution_clock()
                left = remaining(deadline, now)
                if left <= 0:
                    self.deadline_receipt = {
                        "status": "not_dispatched_deadline",
                        "deadline_monotonic": deadline,
                    }
                    # The original step reserved the output budget before calling
                    # generate; no request was dispatched in this branch.
                    self.usage["unknown_generation_reservation"] = 0
                    raise GenerationDeadline()
                wire = dict(request, deadline_monotonic=deadline)
                response = request_json(url, wire, timeout=left + 5)
                if response.get("finish_reason") == "deadline":
                    tokens = validate_deadline_response(response, wire)
                    self.usage["generated_tokens"] += len(tokens)
                    self.usage["unknown_generation_reservation"] = 0
                    self.deadline_receipt = response
                    if self.output:
                        number = self.usage["model_requests"]
                        write_json(
                            self.output
                            / "requests"
                            / f"{number:03d}.deadline_response.json",
                            response,
                        )
                    raise GenerationDeadline()
                return response

            try:
                return super().run(generate)
            except GenerationDeadline:
                original_termination = self.termination
                self.stopped, self.termination = True, "budget_exhausted"
                audit = {
                    "runtime_variant": VARIANT,
                    "original_step_termination": original_termination,
                    "resolved_termination": self.termination,
                    "receipt": self.deadline_receipt,
                    "partial_tool_calls_executed": False,
                    "usage": self.usage,
                }
                if self.output:
                    write_json(
                        self.output / "generation_deadline_resolution.json", audit
                    )
                return {
                    "actor_role": "student",
                    "termination": self.termination,
                    "usage": self.usage,
                    "rl_eligible": False,
                    "training_started": False,
                    "policy": self.identity,
                    "generation_deadline_resolution": audit,
                }

    runner.StudentSession = Session
    return runner


def serve(args):
    import torch
    from peft import PeftModel, get_peft_model_state_dict
    from transformers import (
        AutoModelForCausalLM,
        StoppingCriteria,
        StoppingCriteriaList,
    )
    from .sft_recipe import tensor_hash

    accepted = read_json(args.probe)
    validate_probe(accepted, args.parameter_hash)
    metadata = original.verified_checkpoint(args.adapter)
    if metadata["trainable_after_hash"] != args.parameter_hash:
        raise ValueError("Wrong M1 checkpoint")
    torch.set_num_threads(4)
    if not torch.cuda.is_bf16_supported():
        raise ValueError("Native BF16 required")
    tokenizer = load_tokenizer()
    model = AutoModelForCausalLM.from_pretrained(
        MODEL,
        revision=REVISION,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    model = PeftModel.from_pretrained(model, args.adapter, is_trainable=False)
    if tensor_hash(get_peft_model_state_dict(model)) != args.parameter_hash:
        raise ValueError("Loaded adapter hash mismatch")
    model = model.to("cuda").eval()
    identity = {
        "model": MODEL,
        "revision": REVISION,
        "thinking": False,
        "chat_template_sha256": TEMPLATE_SHA256,
        "checkpoint_id": "adapter:" + args.parameter_hash,
    }
    runtime = {
        "runtime_variant": VARIANT,
        "source_lock_sha256": verify_lock(),
        "mode": "original_unmerged_peft_bf16",
        "merge_optimization_accepted": False,
        "probe_sha256": sha256(args.probe),
        "gpu": torch.cuda.get_device_name(),
        "policy": identity,
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "identity.json", runtime)

    class Deadline(StoppingCriteria):
        def __init__(self, deadline):
            self.deadline, self.expired = deadline, False

        def __call__(self, input_ids, scores, **kwargs):
            self.expired = execution_clock() >= self.deadline
            return self.expired

    class Handler(BaseHTTPRequestHandler):
        def respond(self, payload, status=200):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.respond(runtime if self.path == "/runtime" else identity)

        def do_POST(self):
            started = execution_clock()
            try:
                size = int(self.headers["Content-Length"])
                if not 1 <= size <= 4 * 1024 * 1024:
                    raise ValueError("Invalid request size")
                request = json.loads(self.rfile.read(size))
                deadline = request["deadline_monotonic"]
                left = remaining(deadline, started)
                ids = validate_request(request, identity, tokenizer)
                stopper = Deadline(deadline)
                tokens = []
                torch.cuda.reset_peak_memory_stats()
                if left > 0 and execution_clock() < deadline:
                    inputs = torch.tensor([ids], dtype=torch.long, device="cuda")
                    torch.manual_seed(request["seed"])
                    with (
                        torch.inference_mode(),
                        torch.autocast("cuda", dtype=torch.bfloat16),
                    ):
                        result = model.generate(
                            input_ids=inputs,
                            attention_mask=torch.ones_like(inputs),
                            max_new_tokens=request["max_new_tokens"],
                            do_sample=True,
                            temperature=request["temperature"],
                            top_p=request["top_p"],
                            pad_token_id=tokenizer.pad_token_id,
                            eos_token_id=tokenizer.eos_token_id,
                            use_cache=True,
                            stopping_criteria=StoppingCriteriaList([stopper]),
                        )
                    tokens = result[0, len(ids) :].tolist()
                expired = stopper.expired or execution_clock() >= deadline
                response = {
                    "policy": identity,
                    "input_token_ids": ids,
                    "token_ids": tokens,
                    "finish_reason": "deadline"
                    if expired
                    else "eos"
                    if tokens and tokens[-1] == tokenizer.eos_token_id
                    else "length",
                    "runtime_variant": VARIANT,
                    "deadline_monotonic": deadline,
                    "generation_seconds": execution_clock() - started,
                    "deadline_overshoot_seconds": max(0, execution_clock() - deadline),
                    "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                }
                self.respond(response)
            except Exception as e:
                with (output / "errors.jsonl").open("a") as f:
                    f.write(
                        json.dumps(
                            {"time": time.time(), "error_type": type(e).__name__}
                        )
                        + "\n"
                    )
                self.respond({"error_type": type(e).__name__}, status=500)

        def log_message(self, *args):
            pass

    print(json.dumps({"status": "READY", **runtime}), flush=True)
    HTTPServer(("127.0.0.1", 8768), Handler).serve_forever()


def worker(args):
    cfg = read_json(args.config)
    original.validate_config(cfg)
    if not 46 <= args.case_index < len(cfg["cases"]):
        raise ValueError("Never retry the 46 prior C244 attempts")
    result = deadline_engine(cfg["inference_url"]).run_episode(
        cfg, cfg["cases"][args.case_index], "base_sft", args.output
    )
    result.update(
        scope=original.SCOPE,
        stage="M1",
        official_benchmark_claim=False,
        runtime_variant=VARIANT,
        source_lock_sha256=verify_lock(),
    )
    write_json(Path(args.output) / "full732_result.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "worker"])
    for name in ("adapter", "probe", "parameter-hash", "config", "output"):
        parser.add_argument("--" + name)
    parser.add_argument("--case-index", type=int)
    args = parser.parse_args()
    verify_lock()
    serve(args) if args.command == "serve" else worker(args)


if __name__ == "__main__":
    main()
