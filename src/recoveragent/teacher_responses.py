"""Lossless Responses API boundary for the existing Teacher tool loop.

Actual wire requests and original Responses objects remain in private receipts.
Only public Assistant text/function calls enter the shared execution/training history.
"""

import copy

PROTOCOL = "openai_responses_v1"


def build_request(messages, tools, max_tokens, teacher, previous_outputs):
    items = []
    for index, message in enumerate(messages):
        role = message["role"]
        if role == "assistant" and index in previous_outputs:
            items.extend(copy.deepcopy(previous_outputs[index]))
        elif role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message["content"],
                }
            )
        elif role == "assistant":
            if message.get("content"):
                items.append({"role": role, "content": message["content"]})
            for call in message.get("tool_calls", []):
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        **copy.deepcopy(call["function"]),
                    }
                )
        else:
            items.append({"role": role, "content": message["content"]})
    return {
        "model": teacher["model"],
        "input": items,
        "tools": [
            {"type": "function", **copy.deepcopy(t["function"]), "strict": False}
            for t in tools
        ],
        "max_output_tokens": max_tokens,
        "parallel_tool_calls": False,
        "store": False,
        "stream": False,
    }


def project_response(raw):
    """Preserve the raw object and map exact text/calls/usage, never repair output."""
    message = {"role": "assistant", "content": ""}
    calls, text = [], []
    supported = True
    for item in raw.get("output", []):
        if item["type"] == "function_call":
            calls.append(
                {
                    "id": item["call_id"],
                    "type": "function",
                    "function": {
                        "name": item["name"],
                        "arguments": item["arguments"],
                    },
                }
            )
            supported &= item.get("status", "completed") == "completed"
        elif item["type"] == "message" and item.get("role") == "assistant":
            for part in item["content"]:
                if part["type"] == "output_text":
                    text.append(part["text"])
                else:
                    supported = False
            supported &= item.get("status", "completed") == "completed"
        elif item["type"] != "reasoning":
            supported = False
    message["content"] = "".join(text)
    if calls:
        message["tool_calls"] = calls
    finish = "tool_calls" if calls else "stop"
    if raw.get("status") != "completed" or not supported:
        finish = (
            "length"
            if (raw.get("incomplete_details") or {}).get("reason")
            == "max_output_tokens"
            else "unsupported"
        )
    usage = raw["usage"]
    return {
        "id": raw["id"],
        "model": raw["model"],
        "system_fingerprint": raw.get("system_fingerprint"),
        "choices": [{"finish_reason": finish, "message": message}],
        "usage": {
            "prompt_tokens": usage["input_tokens"],
            "completion_tokens": usage["output_tokens"],
            "prompt_tokens_details": {
                "cached_tokens": usage.get("input_tokens_details", {}).get(
                    "cached_tokens", 0
                )
            },
        },
        "responses_raw": copy.deepcopy(raw),
    }


def validate_projection(response):
    if response != project_response(response["responses_raw"]):
        raise ValueError("Responses projection differs from original provider receipt")
