"""New S0 evaluation uses the same CodeAct loop and original official evaluator."""

from pathlib import Path

from recoveragent.io import digest, read_json, write_json
from recoveragent.teacher import TeacherTransportError
from recoveragent.v2_model import load_base, load_tokenizer, parse_native
from recoveragent.v2_tasks import plan
from recoveragent.v2_training import verify_checkpoint

from .episode import run
from .protocol import TOOLS, BudgetExhausted, identity, now


class Student:
    def __init__(self, config, adapter):
        from peft import PeftModel

        receipt = verify_checkpoint(adapter, config["model"])
        if receipt.get("protocol") != identity() or not receipt.get("formal_s0"):
            raise ValueError(
                "Evaluation requires this new main line's formal S0, not old or smoke weights"
            )
        self.config = config
        self.model = PeftModel.from_pretrained(
            load_base(config["model"]), adapter
        ).eval()
        self.tokenizer = load_tokenizer(config["model"])
        self.model_identity = {
            "base": config["model"],
            "adapter": str(Path(adapter).resolve()),
            "receipt": receipt,
        }
        self.reset_usage()

    def reset_usage(self):
        self.started = now()
        self.usage = {
            "requests": 0,
            "prompt_tokens": 0,
            "generated_tokens": 0,
            "cached_prompt_tokens": 0,
            "priced_cost": 0,
            "provider_cost": 0,
            "seconds": 0,
        }

    def remember(self, index, response):
        pass  # Student is non-thinking; raw token receipts are already saved.

    def prompt(self, messages, directory, index, remaining_tokens):
        stage = self.config["student_inference"]
        ids = self.tokenizer.apply_chat_template(
            messages,
            tools=TOOLS,
            tokenize=True,
            return_dict=False,
            enable_thinking=False,
            add_generation_prompt=True,
        )
        maximum = min(stage["max_new_tokens"], remaining_tokens)
        if len(ids) + maximum > stage["max_sequence_tokens"]:
            raise BudgetExhausted("Student 64K context exhausted; no truncation")
        write_json(
            directory / f"{index:03d}.request.json",
            {
                "messages": messages,
                "tools": TOOLS,
                "input_token_ids": ids,
                "max_new_tokens": maximum,
                "settings": stage,
            },
        )
        return ids, maximum

    def generate(self, messages, directory, index, remaining_tokens):
        import torch

        stage = self.config["student_inference"]
        ids, maximum = self.prompt(messages, directory, index, remaining_tokens)
        started = now()
        tensor = torch.tensor([ids], device=self.model.device)
        with torch.inference_mode():
            result = self.model.generate(
                input_ids=tensor,
                attention_mask=torch.ones_like(tensor),
                max_new_tokens=maximum,
                do_sample=stage["temperature"] > 0,
                **(
                    {"temperature": stage["temperature"], "top_p": stage["top_p"]}
                    if stage["temperature"] > 0
                    else {}
                ),
            )
        generated = result[0, len(ids) :].tolist()
        elapsed = now() - started
        return self.finish(ids, generated, elapsed, directory, index)

    def finish(self, ids, generated, elapsed, directory, index, *, finish_reason=None):
        text = self.tokenizer.decode(generated, skip_special_tokens=False)
        ended = bool(generated) and generated[-1] in {
            self.tokenizer.eos_token_id,
            self.tokenizer.convert_tokens_to_ids("<|im_end|>"),
        }
        if finish_reason is not None:
            # Providers can omit stop tokens from returned IDs; keep IDs unchanged.
            ended = finish_reason == "stop"
        write_json(
            directory / f"{index:03d}.tokens.json",
            {
                "input_ids": ids,
                "generated_ids": generated,
                "text": text,
                "seconds": elapsed,
                "ended": ended,
                "provider_finish_reason": finish_reason,
            },
        )
        self.usage["requests"] += 1
        self.usage["prompt_tokens"] += len(ids)
        self.usage["generated_tokens"] += len(generated)
        self.usage["seconds"] += elapsed
        try:
            message = parse_native(
                text,
                f"codeact_{index}",
                "stop" if ended else "length",
                TOOLS,
                self.config["model"],
            )
            finish = "tool_calls" if message.get("tool_calls") else "stop"
        except ValueError:
            message, finish = (
                {"role": "assistant", "content": text},
                "length" if not ended else "stop",
            )
        response = {
            "model": self.config["model"]["model"],
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": len(ids), "completion_tokens": len(generated)},
        }
        write_json(directory / f"{index:03d}.response.json", response)
        return response


class VLLMStudent(Student):
    """Same public input/parser/loop; only the native-template token backend differs."""

    def __init__(self, config, adapter, url, server_receipt):
        import requests

        checkpoint = verify_checkpoint(adapter, config["model"])
        proof = read_json(server_receipt)
        if (
            checkpoint.get("protocol") != identity()
            or not checkpoint.get("formal_s0")
            or proof.get("protocol") != identity()
            or proof.get("base") != config["model"]
            or proof.get("adapter_receipt_sha256") != digest(checkpoint)
            or proof.get("max_model_len") != 65536
            or proof.get("dtype") != "bfloat16"
            or proof.get("vllm_version") != "0.19.0"
            or proof.get("url") != url
            or not url.startswith("http://127.0.0.1:")
        ):
            raise ValueError("CodeAct vLLM server/checkpoint identity mismatch")
        self.config, self.url, self.server = config, url, proof
        self.tokenizer = load_tokenizer(config["model"])
        self.http = requests.Session()
        self.http.trust_env = False
        self.model_identity = {
            "base": config["model"],
            "adapter": str(Path(adapter).resolve()),
            "receipt": checkpoint,
            "backend": proof,
        }
        self.reset_usage()

    def generate(self, messages, directory, index, remaining_tokens):
        import requests

        ids, maximum = self.prompt(messages, directory, index, remaining_tokens)
        remaining = self.config["limits"]["wall_seconds"] - (now() - self.started)
        if remaining <= 0:
            raise BudgetExhausted("Student session deadline before vLLM dispatch")
        stage = self.config["student_inference"]
        payload = {
            "model": self.server["model_name"],
            "prompt": ids,
            "max_tokens": maximum,
            "temperature": stage["temperature"],
            "top_p": stage["top_p"],
            "top_k": -1,
            "seed": self.config["seed"],
            "return_token_ids": True,
            "skip_special_tokens": False,
            "stop_token_ids": [self.tokenizer.eos_token_id],
            "ignore_eos": False,
            "stream": False,
        }
        write_json(directory / f"{index:03d}.backend_request.json", payload)
        started = now()
        try:
            response = self.http.post(
                self.url + "/v1/completions", json=payload, timeout=remaining
            )
            response.raise_for_status()
            value = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise TeacherTransportError(
                "Student vLLM request failed: " + str(exc)
            ) from exc
        elapsed = now() - started
        write_json(directory / f"{index:03d}.backend_response.json", value)
        choices = value.get("choices", [])
        generated = choices[0].get("token_ids") if len(choices) == 1 else None
        if (
            value.get("model") != self.server["model_name"]
            or not isinstance(generated, list)
            or not generated
            or any(type(i) is not int or i < 0 for i in generated)
            or len(generated) > maximum
            or value.get("usage", {}).get("prompt_tokens") != len(ids)
            or value.get("usage", {}).get("completion_tokens") != len(generated)
            or choices[0].get("finish_reason") not in ("stop", "length")
        ):
            raise ValueError("Invalid CodeAct vLLM token/model/usage receipt")
        return self.finish(
            ids,
            generated,
            elapsed,
            directory,
            index,
            finish_reason=choices[0]["finish_reason"],
        )


def evaluation_tasks(split, task_set):
    frozen = plan()
    if task_set == "dev24":
        if split != "dev":
            raise ValueError("dev24 may only evaluate the frozen dev tasks")
        return list(frozen["dev24"])
    if task_set != "all":
        raise ValueError("Unknown evaluation task set")
    return [r["task_id"] for r in frozen["rows"] if r["role"] == split]


def evaluate(
    config,
    output,
    root,
    adapter,
    split,
    *,
    task_set="all",
    vllm_url=None,
    server_receipt=None,
):
    from .collect import replace_json

    directory = Path(output).resolve()
    tasks = evaluation_tasks(split, task_set)
    checkpoint = verify_checkpoint(adapter, config["model"])
    binding = {
        "protocol": identity(),
        "config": config,
        "adapter": str(Path(adapter).resolve()),
        "tasks": tasks,
        "task_set": task_set,
        "adapter_receipt_sha256": digest(checkpoint),
        "backend": read_json(server_receipt) if vllm_url else "transformers",
    }
    if directory.exists():
        if read_json(directory / "binding.json") != binding:
            raise ValueError("Evaluation binding mismatch")
    else:
        write_json(directory / "binding.json", binding)
    client = (
        VLLMStudent(config, adapter, vllm_url, server_receipt)
        if vllm_url
        else Student(config, adapter)
    )
    results = []

    def progress(status):
        replace_json(
            directory / "summary.json",
            {
                "status": status,
                "requested_tasks": len(tasks),
                "tasks": len(results),
                "success": sum(r["success"] is True for r in results),
                "failure": sum(r["success"] is False for r in results),
                "unknown": sum(r["success"] is None for r in results),
                "seconds": sum(r.get("seconds", 0) for r in results),
                "usage": {
                    k: sum(r.get("usage", {}).get(k, 0) for r in results)
                    for k in (
                        "requests",
                        "prompt_tokens",
                        "generated_tokens",
                        "seconds",
                    )
                },
            },
        )

    progress("running")
    for task in tasks:
        target = directory / task
        if (target / "result.json").exists():
            results.append(read_json(target / "result.json"))
            continue
        client.reset_usage()
        results.append(
            run(task, 1, target, root, config, client, training=split == "train")
        )
        progress("running")
        if results[-1]["termination"] == "teacher_service_error":
            progress("backend_blocked")
            return
    progress("complete")
