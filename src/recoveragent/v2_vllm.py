"""Token-exact client for an isolated, pinned vLLM LoRA server."""

import math
import time

from .io import read_json
from .v2_context import validate_request_context


def request_payload(request, identity, tokenizer, model_name):
    validate_request_context(request, 65536)
    text = tokenizer.apply_chat_template(
        request["messages"],
        tools=request["tools"],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    if (
        request["policy"] != identity
        or tokenizer(text, add_special_tokens=False)["input_ids"]
        != request["input_token_ids"]
    ):
        raise ValueError("vLLM policy or actual input differs from Student receipt")
    if request["top_p"] != 1.0:
        raise ValueError("Unsupported sampling distribution")
    return {
        "model": model_name,
        "prompt": request["input_token_ids"],
        "max_tokens": request["max_new_tokens"],
        "temperature": request["temperature"],
        "top_p": 1.0,
        "top_k": -1,
        "seed": request["seed"],
        "logprobs": 1,
        "return_token_ids": True,
        "return_tokens_as_token_ids": True,
        "skip_special_tokens": False,
        "stop_token_ids": [request["eos_token_id"]],
        "ignore_eos": False,
        "stream": False,
    }


def response_receipt(value, request, identity, tokenizer, model_name, seconds):
    if value.get("model") != model_name or len(value.get("choices", [])) != 1:
        raise ValueError("vLLM response model or multiplicity changed")
    choice = value["choices"][0]
    ids, logps = (
        choice.get("token_ids"),
        choice.get("logprobs", {}).get("token_logprobs"),
    )
    if (
        not isinstance(ids, list)
        or not isinstance(logps, list)
        or len(ids) != len(logps)
        or len(ids) > request["max_new_tokens"]
        or any(type(t) is not int for t in ids)
        or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in logps)
        or value["usage"]["prompt_tokens"] != len(request["input_token_ids"])
        or value["usage"]["completion_tokens"] != len(ids)
    ):
        raise ValueError("Invalid generated token IDs, logprobs, or usage")
    return {
        "policy": identity,
        "input_token_ids": request["input_token_ids"],
        "token_ids": ids,
        "text": tokenizer.decode(ids, skip_special_tokens=False),
        "finish_reason": "eos"
        if ids and ids[-1] == request["eos_token_id"]
        else "length",
        "logprobs": logps,
        "logprob_contract": "vllm_processed_logprobs_temperature_top_p1_top_k_all",
        "generation_seconds": seconds,
        "remaining_wall_seconds_at_dispatch": request["remaining_wall_seconds"],
        "backend": "vllm_0.19.0",
        "provider_response_id": value.get("id"),
    }


class Backend:
    def __init__(self, url, receipt, identity, model_name):
        import requests

        proof = read_json(receipt)
        if (
            proof["policy"] != identity
            or proof["model_name"] != model_name
            or proof["max_model_len"] != 65536
            or proof["dtype"] != "bfloat16"
            or proof["vllm_version"] != "0.19.0"
            or proof["logprobs_mode"] != "processed_logprobs"
        ):
            raise ValueError("vLLM startup identity/configuration mismatch")
        if not url.startswith("http://127.0.0.1:"):
            raise ValueError("This private runtime requires a local vLLM server")
        self.url, self.identity, self.model_name = url, identity, model_name
        self.http = requests.Session()
        self.http.trust_env = False

    def __call__(
        self,
        model,
        tokenizer,
        request,
        *,
        identity,
        deadline,
        max_input_tokens,
        token_ledger=None,
    ):
        if (
            identity != self.identity
            or max_input_tokens != 65536
            or token_ledger is not None
        ):
            raise ValueError("Backend binding or ledger mode mismatch")
        remaining = min(deadline - time.monotonic(), request["remaining_wall_seconds"])
        if remaining <= 0:
            raise TimeoutError("Session deadline reached before vLLM dispatch")
        payload = request_payload(request, identity, tokenizer, self.model_name)
        started = time.monotonic()
        response = self.http.post(
            self.url + "/v1/completions", json=payload, timeout=remaining
        )
        response.raise_for_status()
        return response_receipt(
            response.json(),
            request,
            identity,
            tokenizer,
            self.model_name,
            time.monotonic() - started,
        )
