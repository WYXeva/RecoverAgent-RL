"""Verify the pinned S0 LoRA server with short and two full-window requests."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import time
from pathlib import Path

import requests

from recoveragent.io import digest, read_json, write_json
from recoveragent.v2_model import load_tokenizer, verify_weights
from recoveragent.v2_s1_batch import checkpoint, configuration
from recoveragent.v2_vllm import request_payload, response_receipt


def probe(deployment, output):
    cfg = configuration()
    identity = cfg["models"][cfg["selected_model"]]
    checkpoint_receipt = checkpoint(deployment["s0"], cfg, initial=True)
    verify_weights(identity)
    tokenizer = load_tokenizer(identity)
    policy = {
        **identity,
        "checkpoint_id": digest(
            {"adapter_sha256": checkpoint_receipt["adapter_sha256_after"]}
        ),
    }
    pid = int(Path(deployment["vllm_pidfile"]).read_text())
    command = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
    expected = {
        "--dtype": "bfloat16",
        "--max-model-len": "65536",
        "--logprobs-mode": "processed_logprobs",
        "--model": deployment["model_snapshot"],
        "--max-num-seqs": str(cfg["inference"]["max_num_seqs"]),
        "--lora-modules": "S0_base=" + deployment["s0"],
        "--host": "127.0.0.1",
        "--port": "18000",
    }
    if any(
        flag not in command or command[command.index(flag) + 1] != value
        for flag, value in expected.items()
    ):
        raise ValueError(
            "Server command differs from frozen checkpoint/context configuration"
        )
    version = subprocess.check_output(
        [
            deployment["vllm_python"],
            "-c",
            "from importlib.metadata import version; print(version('vllm'))",
        ],
        text=True,
    ).strip()
    if version != cfg["inference"]["version"]:
        raise ValueError("vLLM version differs")
    http = requests.Session()
    http.trust_env = False
    models = http.get(deployment["vllm_url"] + "/v1/models", timeout=10).json()["data"]
    if not any(
        m["id"] == "S0_base" and m.get("root") == deployment["s0"] for m in models
    ):
        raise ValueError("Accepted S0 adapter is not served")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)

    def run(label, messages, reserve, model="S0_base"):
        text = tokenizer.apply_chat_template(
            messages,
            tools=[],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        ids = tokenizer(text, add_special_tokens=False)["input_ids"]
        q = {
            "policy": policy,
            "messages": messages,
            "tools": [],
            "input_token_ids": ids,
            "max_input_tokens": 65536,
            "max_new_tokens": reserve,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 42,
            "eos_token_id": tokenizer.eos_token_id,
            "remaining_wall_seconds": 1200.0,
        }
        payload = request_payload(q, policy, tokenizer, model)
        payload["temperature"] = (
            0.0  # deterministic synthetic correctness/lineage probe only
        )
        write_json(output / (label + ".request.json"), q)
        started = time.monotonic()
        response = http.post(
            deployment["vllm_url"] + "/v1/completions", json=payload, timeout=1200
        )
        response.raise_for_status()
        value = response.json()
        write_json(output / (label + ".provider.json"), value)
        receipt = response_receipt(
            value, q, policy, tokenizer, model, time.monotonic() - started
        )
        write_json(output / (label + ".response.json"), receipt)
        return {
            "label": label,
            "input_tokens": len(ids),
            "generated_tokens": len(receipt["token_ids"]),
            "seconds": receipt["generation_seconds"],
            "token_ids": receipt["token_ids"],
            "logprobs": receipt["logprobs"],
        }

    messages = [{"role": "user", "content": "Return only the word ready."}]
    short = run("short", messages, 128)
    repeated = run("short_cached", messages, 128)
    if short["token_ids"] != repeated["token_ids"]:
        raise ValueError("Prefix cache changed greedy synthetic output")
    base = run("raw_base_control", messages, 128, model="Qwen3.5-9B-pinned")
    if (
        short["token_ids"] == base["token_ids"]
        and short["logprobs"] == base["logprobs"]
    ):
        raise ValueError("No observable adapter effect on synthetic control")

    # Build an exact complete sequence, never truncate a real task/history.
    def long_input(label):
        content = label + " Repeat the final word: " + " x" * 65500
        for _ in range(5):
            messages = [{"role": "user", "content": content}]
            text = tokenizer.apply_chat_template(
                messages,
                tools=[],
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            count = len(tokenizer(text, add_special_tokens=False)["input_ids"])
            delta = 65520 - count
            if delta == 0:
                return messages
            if abs(delta) > 200:
                raise ValueError("Unexpected synthetic padding tokenization")
            content = content + " x" * delta if delta > 0 else content[: 2 * delta]
        raise ValueError("Cannot construct exact synthetic context")

    inputs = [long_input(label) for label in ("A", "B")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        long = list(pool.map(lambda i: run(f"long_{i}", inputs[i], 16), range(2)))
    summary = {
        "passed": True,
        "synthetic_only": True,
        "real_tasks": 0,
        "policy": policy,
        "probes": [short, repeated, base, *long],
        "server_command": command,
        "vllm_version": version,
        "model_name": "S0_base",
        "max_model_len": 65536,
        "dtype": "bfloat16",
        "logprobs_mode": "processed_logprobs",
        "adapter_files": checkpoint_receipt["files"],
        "checked_at_epoch": time.time(),
    }
    write_json(output / "summary.json", summary)
    write_json(deployment["vllm_receipt"], summary)
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k not in {"adapter_files", "server_command"}
            }
        ),
        flush=True,
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    probe(read_json(args.deployment), args.output)
