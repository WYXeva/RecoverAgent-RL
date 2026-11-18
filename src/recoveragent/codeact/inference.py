"""New S0 evaluation uses the same CodeAct loop and original official evaluator."""

from pathlib import Path

from recoveragent.io import read_json, write_json
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

    def generate(self, messages, directory, index, remaining_tokens):
        import torch

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
        text = self.tokenizer.decode(generated, skip_special_tokens=False)
        ended = generated[-1] in {
            self.tokenizer.eos_token_id,
            self.tokenizer.convert_tokens_to_ids("<|im_end|>"),
        }
        write_json(
            directory / f"{index:03d}.tokens.json",
            {
                "input_ids": ids,
                "generated_ids": generated,
                "text": text,
                "seconds": elapsed,
                "ended": ended,
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


def evaluate(config, output, root, adapter, split):
    directory = Path(output).resolve()
    tasks = [r["task_id"] for r in plan()["rows"] if r["role"] == split]
    binding = {
        "protocol": identity(),
        "config": config,
        "adapter": str(Path(adapter).resolve()),
        "tasks": tasks,
    }
    if directory.exists():
        if read_json(directory / "binding.json") != binding:
            raise ValueError("Evaluation binding mismatch")
    else:
        write_json(directory / "binding.json", binding)
    client = Student(config, adapter)
    results = []
    for task in tasks:
        target = directory / task
        if (target / "result.json").exists():
            results.append(read_json(target / "result.json"))
            continue
        client.reset_usage()
        results.append(
            run(task, 1, target, root, config, client, training=split == "train")
        )
    from .collect import replace_json

    replace_json(
        directory / "summary.json",
        {
            "tasks": len(results),
            "success": sum(r["success"] is True for r in results),
            "failure": sum(r["success"] is False for r in results),
            "unknown": sum(r["success"] is None for r in results),
        },
    )
