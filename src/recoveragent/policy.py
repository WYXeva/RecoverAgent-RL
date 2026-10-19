"""Local inference transport and conservative Qwen text-function-call parsing."""

import json
import re
import urllib.request


class PolicyError(RuntimeError):
    pass


def parse_completion(text, call_id):
    blocks = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", text, flags=re.S)
    if len(blocks) != 1:
        raise ValueError("Return exactly one complete <tool_call> JSON object per turn")
    value = json.loads(blocks[0])
    if not isinstance(value, dict) or not isinstance(value.get("arguments"), dict):
        raise ValueError("Function call requires name and object arguments")
    if not isinstance(value.get("name"), str):
        raise ValueError("Function name must be text")
    return {
        "role": "assistant",
        "content": text.split("<tool_call>")[0],
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": value["name"],
                    "arguments": json.dumps(value["arguments"], ensure_ascii=False),
                },
            }
        ],
    }


class LocalPolicy:
    def __init__(self, url, timeout=120):
        if not url.startswith("http://127.0.0.1:"):
            raise ValueError("P0–P2 defaults to local inference only")
        self.url = url.rstrip("/")
        self.timeout = timeout

    def generate(self, messages, tools, seed, max_new_tokens, config):
        payload = {
            "messages": messages,
            "tools": tools,
            "seed": seed,
            "max_new_tokens": max_new_tokens,
            "temperature": config["temperature"],
            "top_p": config["top_p"],
            "max_input_tokens": config["max_input_tokens"],
        }
        request = urllib.request.Request(
            self.url + "/generate",
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            value = json.load(response)
        if "error" in value:
            raise PolicyError(value["error"])
        return value
