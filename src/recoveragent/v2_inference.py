"""Local GPU generation and isolated environment RPC with one session deadline."""

import json
import selectors
import subprocess
import time
from pathlib import Path

from .io import digest, write_json
from .v2_context import validate_request_context


def generate(
    model,
    tokenizer,
    request,
    *,
    identity,
    deadline,
    max_input_tokens,
    token_ledger=None,
):
    validate_request_context(request, max_input_tokens)
    import torch
    from transformers import GenerationConfig, StoppingCriteria, StoppingCriteriaList

    if request["policy"] != identity:
        raise ValueError("Worker/backend policy mismatch")
    text = tokenizer.apply_chat_template(
        request["messages"],
        tools=request["tools"],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    if (
        tokenizer(text, add_special_tokens=False)["input_ids"]
        != request["input_token_ids"]
    ):
        raise ValueError("Worker/backend template or request mismatch")
    if request["top_p"] != 1.0:
        raise ValueError("v2 on-policy sampler uses temperature with top_p=1, top_k=0")
    until = min(deadline, time.monotonic() + request["remaining_wall_seconds"])
    if time.monotonic() >= until:
        raise TimeoutError("Generation deadline reached before dispatch")
    if (
        token_ledger is not None
        and token_ledger["used"] + request["max_new_tokens"] > token_ledger["limit"]
    ):
        raise RuntimeError("Training-side generation cap; no unreserved request")

    class Deadline(StoppingCriteria):
        def __call__(self, input_ids, scores, **kwargs):
            return time.monotonic() >= until

    torch.manual_seed(request["seed"])
    torch.cuda.manual_seed_all(request["seed"])
    model.eval()
    prompt = torch.tensor([request["input_token_ids"]], device=model.device)
    started = time.monotonic()
    with torch.no_grad():
        result = model.generate(
            input_ids=prompt,
            attention_mask=torch.ones_like(prompt),
            generation_config=GenerationConfig(
                max_new_tokens=request["max_new_tokens"],
                do_sample=True,
                temperature=request["temperature"],
                top_p=1.0,
                top_k=0,
                eos_token_id=request["eos_token_id"],
                pad_token_id=tokenizer.pad_token_id,
                use_cache=True,
                return_dict_in_generate=True,
                output_scores=True,
            ),
            stopping_criteria=StoppingCriteriaList([Deadline()]),
        )
    tokens = result.sequences[0, len(request["input_token_ids"]) :].tolist()
    logps = [
        float(torch.log_softmax(score[0].float(), -1)[token])
        for score, token in zip(result.scores, tokens, strict=True)
    ]
    if not all(__import__("math").isfinite(x) for x in logps):
        raise ValueError("Nonfinite behavior logprob")
    if token_ledger is not None:
        token_ledger["used"] += len(tokens)
    return {
        "policy": identity,
        "input_token_ids": request["input_token_ids"],
        "token_ids": tokens,
        "text": tokenizer.decode(tokens, skip_special_tokens=False),
        "finish_reason": "eos"
        if tokens and tokens[-1] == request["eos_token_id"]
        else "length",
        "logprobs": logps,
        "logprob_contract": "temperature_scaled_top_p1_top_k0_actual_sampler",
        "generation_seconds": time.monotonic() - started,
        "remaining_wall_seconds_at_dispatch": request["remaining_wall_seconds"],
        "deadline_reached": time.monotonic() >= until,
    }


def episode(
    config, model, tokenizer, *, python, deadline, token_ledger=None, generate_fn=None
):
    output = Path(config["output"])
    config_path = output.parent / (output.name + ".config.json")
    write_json(config_path, config)
    identity = {**config["model"], "checkpoint_id": config["checkpoint_id"]}
    with (output.parent / (output.name + ".worker.log")).open("x") as log:
        process = subprocess.Popen(
            [python, "-m", "recoveragent.v2_episode", str(config_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            bufsize=1,
        )
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Shared GPU job deadline")
                if not selector.select(timeout=min(30, deadline - time.monotonic())):
                    if process.poll() is not None:
                        raise RuntimeError("AppWorld worker exited without a result")
                    continue
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError("AppWorld worker closed without a result")
                value = json.loads(line)
                if "result" in value:
                    process.wait(timeout=30)
                    return value["result"]
                request = value["request"]
                try:
                    response = (generate_fn or generate)(
                        model,
                        tokenizer,
                        request,
                        identity=identity,
                        deadline=deadline,
                        max_input_tokens=config["student_limits"]["max_input_tokens"],
                        token_ledger=token_ledger,
                    )
                except Exception as exc:
                    response = {"error": type(exc).__name__ + ":" + str(exc)}
                    write_json(
                        output / "backend_failure.json",
                        {
                            "request_sha256": digest(request),
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "unknown_generation_reservation": request["max_new_tokens"],
                            "automatic_retry": False,
                        },
                    )
                    process.stdin.write(json.dumps(response) + "\n")
                    process.stdin.flush()
                    # Give the worker time to persist its infrastructure result,
                    # then stop the whole run (including during dev evaluation).
                    if selector.select(timeout=10):
                        process.stdout.readline()
                    raise
                process.stdin.write(json.dumps(response) + "\n")
                process.stdin.flush()
        finally:
            selector.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def policy_id(model):
    from peft import get_peft_model_state_dict
    from .sft_recipe import tensor_hash

    return digest({"adapter_sha256": tensor_hash(get_peft_model_state_dict(model))})
