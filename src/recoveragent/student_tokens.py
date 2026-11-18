"""Versioned append-only Student tokens; structured messages remain audit data.

Only new observations are rendered. Actual sampled IDs (including rejected
replies) are copied verbatim. This is not a context compressor or Teacher format.
"""

import copy

from .io import digest

REVISION = "student_append_tokens_v1"
FORMAT = "qwen-append-generation-v1"
KEY = "student_input_encoding"
NOTICE = (
    "The initial tools header stays fixed. A successful show_api_doc observation "
    "may announce additional tool_definitions with their exact parameter schemas. "
    "Those APIs are callable starting with your NEXT reply, in the same native "
    "Function Calling format and with the same function names. Follow their "
    "required fields, types and constraints. APIs not yet discovered remain "
    "unavailable. Do not call an API in the same batch that discovers it."
)


def discovered_observation(raw, before, after):
    """Reuse the Adapter's public, compatible definitions; do not register APIs."""
    import json
    from .io import canonical
    from .tool_discovery import project_document

    known = {t["function"]["name"] for t in before}
    added = [t for t in after if t["function"]["name"] not in known]
    if not added:
        return raw
    # One public show_api_doc call discovers one API. Keep unknown extra notes.
    compact = project_document(raw, added[0]["function"])
    try:
        observation = json.loads(compact)
    except (TypeError, ValueError):
        observation = compact
    return canonical(
        {
            "availability": "next_assistant_reply",
            "tool_definitions": added,
            "observation": observation,
        }
    )


def encode(tokenizer, text):
    return tokenizer(text, add_special_tokens=False)["input_ids"]


def render(tokenizer, messages, tools, *, generation=True):
    return tokenizer.apply_chat_template(
        messages,
        tools=tools,
        tokenize=False,
        add_generation_prompt=generation,
        enable_thinking=False,
    )


def suffix(tokenizer, generated, messages):
    """Render only new tool/user observations via the pinned native template."""
    observations = [m for m in messages if m["role"] != "assistant"]
    if any(m["role"] not in {"user", "tool"} for m in observations):
        raise ValueError("Unexpected program observation role")
    anchor = [{"role": "user", "content": "Append-only observation boundary"}]
    base = render(tokenizer, anchor, [], generation=False)
    tail = render(tokenizer, anchor + observations, [])
    if not tail.startswith(base):
        raise ValueError("Native observation template is not append compatible")
    # Sampled EOS is retained as a target. Missing EOS is closed by a distinct
    # program span, never assigned a behavior probability or a policy loss.
    close = "\n" if generated[-1] == tokenizer.eos_token_id else "<|im_end|>\n"
    return encode(tokenizer, close + tail[len(base) :])


class TokenHistory:
    def __init__(self, tokenizer, messages, tools):
        self.tokenizer = tokenizer
        self.prompt_tools = copy.deepcopy(tools)
        self.initial_count = len(messages)
        self.ids = encode(tokenizer, render(tokenizer, messages, self.prompt_tools))
        self.initial_hash = digest(self.ids)
        self.turns = []

    def request_fields(self, messages, tools):
        return {
            "format": FORMAT,
            KEY: REVISION,
            "prompt_tools": copy.deepcopy(self.prompt_tools),
            "tools": copy.deepcopy(tools),
            "token_history": {
                "initial_message_count": self.initial_count,
                "initial_token_sha256": self.initial_hash,
                "prompt_tools_sha256": digest(self.prompt_tools),
                "messages_sha256": digest(messages),
                "turns": copy.deepcopy(self.turns),
            },
        }

    def advance(self, request, response, messages, tools):
        if self.ids != request["input_token_ids"]:
            raise ValueError("Committed token history changed during dispatch")
        start = len(request["messages"])
        generated = copy.deepcopy(response["token_ids"])
        program = suffix(self.tokenizer, generated, messages[start:])
        self.turns.append(
            {
                "request_sha256": digest(request),
                "response_sha256": digest(response),
                "input_token_sha256": digest(self.ids),
                "generated_ids": generated,
                "logprobs_sha256": digest(response["logprobs"]),
                "program_ids": program,
                "message_start": start,
                "message_end": len(messages),
                "messages_sha256": digest(messages),
                "tools_sha256": digest(tools),
            }
        )
        self.ids += generated + program


def validate_request(request, tokenizer=None):
    """Serving checks native initial/suffix encoding; offline checks stored spans.

    Receipt-to-receipt authentication against the real previous Y is performed
    separately by validate_transition, in both the RPC loop and GRPO loader.
    """
    revision = request.get(KEY)
    if revision is None:
        if request.get("format") == FORMAT or "token_history" in request:
            raise ValueError("Append request lost its encoding identity")
        if tokenizer is not None:
            ids = encode(
                tokenizer, render(tokenizer, request["messages"], request["tools"])
            )
            if ids != request["input_token_ids"]:
                raise ValueError("Worker/backend template or request mismatch")
        return
    if revision != REVISION or request.get("format") != FORMAT:
        raise ValueError("Unknown Student input encoding")
    proof, messages = request["token_history"], request["messages"]
    count = proof["initial_message_count"]
    if type(count) is not int or not 0 < count <= len(messages):
        raise ValueError("Invalid initial message boundary")
    if proof["messages_sha256"] != digest(messages) or proof[
        "prompt_tools_sha256"
    ] != digest(request["prompt_tools"]):
        raise ValueError("Append prompt or structured history changed")
    # The initial span ends where the first actual reply starts.
    appended = sum(
        len(t["generated_ids"]) + len(t["program_ids"]) for t in proof["turns"]
    )
    initial_length = len(request["input_token_ids"]) - appended
    if initial_length <= 0:
        raise ValueError("Invalid append span lengths")
    ids = request["input_token_ids"][:initial_length]
    if digest(ids) != proof["initial_token_sha256"]:
        raise ValueError("Initial token prefix changed")
    if tokenizer is not None and ids != encode(
        tokenizer, render(tokenizer, messages[:count], request["prompt_tools"])
    ):
        raise ValueError("Initial token template mismatch")
    for turn in proof["turns"]:
        end = turn["message_end"]
        if turn["message_start"] != count or not count <= end <= len(messages):
            raise ValueError("Structured message boundaries changed")
        generated = turn["generated_ids"]
        if not generated or any(
            type(t) is not int or t < 0 for t in generated + turn["program_ids"]
        ):
            raise ValueError("Invalid actual token span")
        if turn["input_token_sha256"] != digest(ids) or turn[
            "messages_sha256"
        ] != digest(messages[:end]):
            raise ValueError("Token/history prefix changed")
        if tokenizer is not None and turn["program_ids"] != suffix(
            tokenizer, generated, messages[count:end]
        ):
            raise ValueError("Program observation encoding changed")
        ids = ids + generated + turn["program_ids"]
        count = end
    if count != len(messages) or ids != request["input_token_ids"]:
        raise ValueError("Actual append sequence differs")
    expected_tools = (
        proof["turns"][-1]["tools_sha256"]
        if proof["turns"]
        else digest(request["prompt_tools"])
    )
    if digest(request["tools"]) != expected_tools:
        raise ValueError("Existing visible tool list changed")


def response_binding(request):
    if request.get(KEY) is None:
        return {}
    return {
        KEY: REVISION,
        "input_proof_sha256": digest(
            {
                "history": request["token_history"],
                "prompt_tools": request["prompt_tools"],
                "tools": request["tools"],
                "ids": request["input_token_ids"],
            }
        ),
    }


def validate_response(request, response):
    if request.get(KEY) != response.get(KEY):
        raise ValueError("Response input encoding mismatch")
    if request.get(KEY) is not None and any(
        response.get(k) != v for k, v in response_binding(request).items()
    ):
        raise ValueError("Response append input proof mismatch")


def validate_transition(request, previous):
    validate_request(request)
    if request.get(KEY) is None:
        if previous is not None and previous[0].get(KEY) is not None:
            raise ValueError("Cannot mix Student input encodings within an episode")
        return
    turns = request["token_history"]["turns"]
    if previous is None:
        if turns:
            raise ValueError("Missing earlier append receipts")
        return
    old, response = previous
    validate_response(old, response)
    if old.get(KEY) != REVISION or not turns:
        raise ValueError("Cannot replace legacy inputs with append encoding")
    if (
        request["prompt_tools"] != old["prompt_tools"]
        or turns[:-1] != old["token_history"]["turns"]
        or any(
            request["token_history"][k] != old["token_history"][k]
            for k in ("initial_message_count", "initial_token_sha256")
        )
    ):
        raise ValueError("Immutable prompt/turn history changed")
    last = turns[-1]
    if (
        last["request_sha256"] != digest(old)
        or last["response_sha256"] != digest(response)
        or last["generated_ids"] != response["token_ids"]
        or last["logprobs_sha256"] != digest(response["logprobs"])
    ):
        raise ValueError("Append history differs from actual sampling receipts")
    prefix = old["input_token_ids"] + response["token_ids"]
    if request["input_token_ids"] != prefix + last["program_ids"]:
        raise ValueError("P[t+1] is not P[t] + Y[t] + observations")
