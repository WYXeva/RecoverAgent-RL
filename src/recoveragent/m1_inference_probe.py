"""Bounded, opt-in GPU comparison of unmerged and in-memory merged M1 LoRA.

Default invocation only prints a plan. No server, training, AppWorld task,
checkpoint write, cloud power operation, or automatic C244 continuation exists.
BF16 merging can change rounding and sampled outputs; this is a new runtime
candidate, never a claim of bitwise equivalence to the sealed C244 backend.
"""

import argparse
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time
import traceback

from .base_sft import MODEL, REVISION, TEMPLATE_SHA256, load_tokenizer
from .demonstrations import sha256
from .full732_c244 import verified_checkpoint, verify_lock as verify_c244_lock
from .full732_m1_backend import validate_request
from .full732_sft_data import ROOT
from .io import read_json, write_json

PLAN = ROOT / "configs/m1_inference_probe_20261030.json"
LOCK = ROOT / "configs/m1_inference_probe_20261030_lock.json"


def verify_source():
    verify_c244_lock()
    for name, expected in read_json(LOCK)["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Unsafe probe source path")
        if sha256(ROOT / name) != expected:
            raise ValueError("Probe source changed: " + name)
    return sha256(LOCK)


def merge_in_memory(model):
    """Never save into the original checkpoint or mutate its on-disk files."""
    merged = model.merge_and_unload(safe_merge=True)
    merged.eval()
    if any("lora_" in name for name, _ in merged.named_parameters()):
        raise ValueError("LoRA branches remain after merge")
    return merged


def probe_requests(tokenizer, identity, plan):
    requests = []
    for repeats in plan["prompt_repetitions"]:
        messages = [
            {"role": "system", "content": "Read the reference text and continue it."},
            {
                "role": "user",
                "content": "Reference record: alpha beta gamma delta.\n" * repeats,
            },
        ]
        text = tokenizer.apply_chat_template(
            messages,
            tools=[],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        request = {
            "policy": identity,
            "messages": messages,
            "tools": [],
            "input_token_ids": tokenizer(text, add_special_tokens=False)["input_ids"],
            "max_new_tokens": plan["output_tokens"],
            "seed": plan["seed"],
            "temperature": plan["temperature"],
            "top_p": plan["top_p"],
        }
        validate_request(request, identity, tokenizer)  # No silent truncation.
        requests.append(request)
    return requests


def measure(model, tokenizer, request, torch):
    inputs = torch.tensor([request["input_token_ids"]], device="cuda")
    torch.manual_seed(request["seed"])
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        result = model.generate(
            input_ids=inputs,
            attention_mask=torch.ones_like(inputs),
            max_new_tokens=request["max_new_tokens"],
            # Synthetic fixed-length throughput probe ONLY. Real task generation
            # must retain its EOS behavior; this function is not a task backend.
            min_new_tokens=request["max_new_tokens"],
            do_sample=True,
            temperature=request["temperature"],
            top_p=request["top_p"],
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
            use_cache=True,
        )
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    tokens = result[0, inputs.shape[1] :].tolist()
    if len(tokens) != request["max_new_tokens"]:
        raise ValueError("Synthetic generation length changed")
    return {
        "input_tokens": inputs.shape[1],
        "output_tokens": len(tokens),
        "token_ids": tokens,
        "generation_seconds": elapsed,
        "tokens_per_second_including_prefill": len(tokens) / elapsed,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
    }


def prefill(model, request, torch):
    inputs = torch.tensor([request["input_token_ids"]], device="cuda")
    torch.cuda.synchronize()
    started = time.perf_counter()
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        logits = model(input_ids=inputs, logits_to_keep=1, use_cache=True).logits
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    if not torch.isfinite(logits).all():
        raise ValueError("Nonfinite probe logits")
    return logits[0, -1].float().cpu(), elapsed


def compare(reference, merged, numeric, plan):
    if (
        len(reference) != len(plan["prompt_repetitions"])
        or len(reference) != len(merged)
        or len(numeric) != len(reference)
    ):
        raise ValueError("Missing paired measurements")
    speedups = []
    for a, b in zip(reference, merged, strict=True):
        if len(a) != plan["repeats"] or len(b) != plan["repeats"]:
            raise ValueError("Incomplete repeats")
        if any(x["output_tokens"] != plan["output_tokens"] for x in a + b):
            raise ValueError("Different generation workload")
        if len({x["input_tokens"] for x in a + b}) != 1:
            raise ValueError("Different prompt workload")
        times = [x["generation_seconds"] for x in a + b]
        if any(not math.isfinite(x) or x <= 0 for x in times):
            raise ValueError("Invalid timing")
        speedups.append(
            statistics.median(times[: len(a)]) / statistics.median(times[len(a) :])
        )
    numerics_ok = all(
        math.isfinite(x["max_abs_logit_error"])
        and x["max_abs_logit_error"] <= plan["numerical_probe_max_abs_error"]
        and x["first_greedy_token_matches"]
        for x in numeric
    )
    return {
        "status": "NUMERICAL_MISMATCH"
        if not numerics_ok
        else "MICROBENCHMARK_PASS"
        if speedups and min(speedups) >= plan["minimum_median_speedup"]
        else "NO_MEASURED_SPEEDUP",
        "per_prompt_median_speedup": speedups,
        "numerical_probe_pass": numerics_ok,
        "bitwise_or_sampled_sequence_equivalence_claim": False,
        "production_activation": False,
    }


def run_worker(adapter, output):
    lock = verify_source()
    plan = read_json(PLAN)
    for package, expected in plan["packages"].items():
        if version(package) != expected:
            raise ValueError("Probe requires the pinned package: " + package)
    import torch
    from transformers import AutoModelForCausalLM
    from peft import PeftModel, get_peft_model_state_dict
    from .sft_recipe import tensor_hash

    if not torch.cuda.is_available():
        raise ValueError("This performance probe requires CUDA; no CPU fallback")
    torch.set_num_threads(4)
    metadata = verified_checkpoint(adapter)
    if metadata["trainable_after_hash"] != plan["adapter_parameter_hash"]:
        raise ValueError("Not the accepted Full732 M1")
    tokenizer = load_tokenizer()
    model = AutoModelForCausalLM.from_pretrained(
        MODEL,
        revision=REVISION,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    model = PeftModel.from_pretrained(model, adapter, is_trainable=False)
    if tensor_hash(get_peft_model_state_dict(model)) != plan["adapter_parameter_hash"]:
        raise ValueError("Loaded LoRA tensors differ")
    model.to("cuda").eval()
    identity = {
        "model": MODEL,
        "revision": REVISION,
        "thinking": False,
        "chat_template_sha256": TEMPLATE_SHA256,
        "checkpoint_id": "adapter:" + plan["adapter_parameter_hash"],
    }
    requests = probe_requests(tokenizer, identity, plan)
    write_json(output / "requests.json", requests)
    result = {
        "source_lock_sha256": lock,
        "plan": plan,
        "policy": identity,
        "gpu": torch.cuda.get_device_name(),
        "packages": plan["packages"],
        "generation_config": model.generation_config.to_dict(),
        "numerical": [],
        "measurements": {},
        "training": False,
        "synthetic_only": True,
        "C244_started": False,
    }
    baseline = []
    for phase in ("reference", "merged"):
        if phase == "merged":
            model = merge_in_memory(model)
        warmup = dict(requests[0], max_new_tokens=plan["warmup_tokens"])
        measure(model, tokenizer, warmup, torch)
        measurements = []
        for i, request in enumerate(requests):
            logits, seconds = prefill(model, request, torch)
            if phase == "reference":
                baseline.append(logits)
            else:
                numeric = {
                    "max_abs_logit_error": (baseline[i] - logits).abs().max().item(),
                    "first_greedy_token_matches": baseline[i].argmax().item()
                    == logits.argmax().item(),
                }
                result["numerical"].append(numeric)
                write_json(output / "progress.json", result)
                if (
                    numeric["max_abs_logit_error"]
                    > plan["numerical_probe_max_abs_error"]
                    or not numeric["first_greedy_token_matches"]
                ):
                    raise ValueError("Merged BF16 numerical probe differs; stop")
            rows = []
            measurements.append(rows)
            result["measurements"][phase] = measurements
            for repeat in range(plan["repeats"]):
                row = measure(model, tokenizer, request, torch)
                row.update(prefill_probe_seconds=seconds, repeat=repeat)
                rows.append(row)
                write_json(output / "progress.json", result)
    # Re-read every original checkpoint file: no merge/save or probe may alter it.
    verified_checkpoint(adapter)
    result.update(
        compare(
            result["measurements"]["reference"],
            result["measurements"]["merged"],
            result["numerical"],
            plan,
        )
    )
    write_json(output / "result.json", result)


def supervise(adapter, output, plan):
    """Hard wall limit includes model loading; kill only our child process group."""
    adapter, output = Path(adapter).resolve(), Path(output).resolve()
    if adapter == output or adapter in output.parents or output in adapter.parents:
        raise ValueError("Probe output must be separate from checkpoint")
    output.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable,
        "-m",
        "recoveragent.m1_inference_probe",
        "--worker",
        "--adapter",
        str(adapter),
        "--output",
        str(output),
    ]
    started = time.monotonic()
    with (output / "worker.log").open("w") as log:
        child = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            code = child.wait(timeout=plan["maximum_wall_seconds"])
        except BaseException as exc:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            write_json(
                output / "supervisor.json",
                {
                    "status": "STOPPED",
                    "error_type": type(exc).__name__,
                    "wall_seconds": time.monotonic() - started,
                    "automatic_retry": False,
                },
            )
            raise
    write_json(
        output / "supervisor.json",
        {
            "status": "EXITED" if code == 0 else "FAILED",
            "returncode": code,
            "wall_seconds": time.monotonic() - started,
            "automatic_retry": False,
        },
    )
    if code:
        raise RuntimeError("GPU probe failed; keep logs, do not retry automatically")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--adapter")
    parser.add_argument("--output")
    args = parser.parse_args()
    lock = verify_source()
    plan = read_json(PLAN)
    if not (args.execute or args.worker):
        print(
            json.dumps(
                {"status": "PLAN_ONLY", "plan": plan, "source_lock_sha256": lock}
            )
        )
        return
    if not args.adapter or not args.output:
        parser.error("Execution needs an existing adapter and a new output directory")
    if args.worker:
        try:
            run_worker(args.adapter, Path(args.output))
        except BaseException as exc:
            write_json(
                Path(args.output) / "failure.json",
                {
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                    "automatic_retry": False,
                },
            )
            raise
    else:
        supervise(args.adapter, args.output, plan)


if __name__ == "__main__":
    main()
