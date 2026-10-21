"""Pinned Qwen3-4B batch Function Calling interface; no weights or training here.

Generation is injected via generate(request) -> receipt. Old P0/P2 transport and
runner stay untouched. Receipts must include actual input/output IDs, EOS/finish
reason and policy identity; this cannot be replaced with fabricated Teacher data.
"""

import copy
import hashlib
import json
import time
from pathlib import Path

from .adapter import call_code
from .base_sft import MODEL, REVISION, TEMPLATE_SHA256
from .io import canonical, digest, write_json
from .teacher_history import validate_history
from .tool_modes import MODES, execute_with_mode
from .answer_guard import AnswerGuard, RULES as ANSWER_RULES


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("Non-finite JSON value")


def parse_batch(text, call_prefix, finish_reason):
    """Parse complete Qwen blocks with JSON decoding, not regex over JSON strings."""
    if finish_reason not in {"stop", "eos"}:
        raise ValueError("Incomplete generation; reject the entire batch")
    # The pinned training template terminates messages with im_end + newline;
    # serving normally stops on im_end itself. Accept both complete forms.
    text = text.rstrip()
    if text.endswith("<|im_end|>"):
        text = text[: -len("<|im_end|>")]
    if "<|im_start|>" in text or "<|im_end|>" in text:
        raise ValueError("Unexpected message boundary")
    # The fixed non-thinking prefix already closes the empty think block.
    if text.lstrip().startswith("<think>"):
        raise ValueError("Unexpected thinking block in non-thinking generation")
    start = text.find("<tool_call>")
    if start < 0:
        if "</tool_call>" in text:
            raise ValueError("Unmatched tool-call closing tag")
        return {"role": "assistant", "content": text.rstrip()}
    content, tail, calls = text[:start].rstrip(), text[start:], []
    if "</tool_call>" in content:
        raise ValueError("Unmatched tool-call closing tag")
    decoder = json.JSONDecoder(object_pairs_hook=_object, parse_constant=_constant)
    while tail.strip():
        tail = tail.lstrip()
        if not tail.startswith("<tool_call>"):
            raise ValueError("Unexpected text between/after tool blocks")
        tail = tail[len("<tool_call>") :].lstrip()
        value, end = decoder.raw_decode(tail)
        tail = tail[end:].lstrip()
        if not tail.startswith("</tool_call>"):
            raise ValueError("Missing closing tool-call tag")
        tail = tail[len("</tool_call>") :]
        if (
            not isinstance(value, dict)
            or set(value) != {"name", "arguments"}
            or not isinstance(value["name"], str)
            or not isinstance(value["arguments"], dict)
        ):
            raise ValueError("Expected a function name and object arguments")
        calls.append(
            {
                "id": f"{call_prefix}_{len(calls)}",
                "type": "function",
                "function": {
                    "name": value["name"],
                    "arguments": canonical(value["arguments"]),
                },
            }
        )
    return {"role": "assistant", "content": content, "tool_calls": calls}


class StudentSession:
    """Serial complete-batch state machine with the exact Base-SFT chat template.

    Tools/arguments are frozen before dispatch; there is no result-reference or
    argument substitution language. New tools/results can only enter the next
    generation. This enforces temporal availability, not arbitrary semantic
    independence of two literal actions. A crash mid-batch is an invalid session,
    never a resumable snapshot or a valid training example.
    """

    def __init__(
        self,
        adapter,
        tokenizer,
        messages,
        tools,
        *,
        tool_mode,
        output=None,
        checkpoint_id=None,
        max_input_tokens=32768,
        max_new_tokens=4096,
        max_requests=40,
        max_generated_tokens=16384,
        wall_seconds=600,
        seed=42,
        temperature=0.8,
        top_p=0.95,
        answer_guard=False,
    ):
        if (
            hashlib.sha256(tokenizer.chat_template.encode()).hexdigest()
            != TEMPLATE_SHA256
        ):
            raise ValueError("Student must use the pinned Qwen3-4B template")
        if tool_mode not in MODES:
            raise ValueError("Unknown tool mode")
        for bound in (
            max_input_tokens,
            max_new_tokens,
            max_requests,
            max_generated_tokens,
        ):
            if type(bound) is not int or bound <= 0:
                raise ValueError("Invalid Student resource bound")
        if max_new_tokens > 4096 or wall_seconds <= 0:
            raise ValueError("Invalid output/time budget")
        validate_history(messages)
        public = {d["function"]["name"]: d for d in adapter.catalogue}
        if any(
            t != public.get(t["function"]["name"])
            or t["function"]["name"].startswith("admin__")
            for t in tools
        ):
            raise ValueError("Non-public or altered initial Student schema")
        self.adapter, self.tokenizer = adapter, tokenizer
        self.answer_guard = None
        if answer_guard:
            if (
                len(messages) < 2
                or messages[0]["role"] != "system"
                or ANSWER_RULES not in messages[0]["content"]
            ):
                raise ValueError("Student answer guard must be declared in the prompt")
            self.answer_guard = AnswerGuard(
                json.loads(messages[1]["content"])["instruction"]
            )
        self.messages, self.tools = copy.deepcopy(messages), copy.deepcopy(tools)
        self.tool_mode = tool_mode
        self.identity = {
            "model": MODEL,
            "revision": REVISION,
            "thinking": False,
            "chat_template_sha256": TEMPLATE_SHA256,
            "checkpoint_id": checkpoint_id or "raw_base:" + REVISION,
        }
        self.output = Path(output) if output else None
        if self.output:
            self.output.mkdir(parents=True, exist_ok=False)
        self.max_input_tokens, self.max_new_tokens = max_input_tokens, max_new_tokens
        self.max_requests, self.max_generated_tokens = (
            max_requests,
            max_generated_tokens,
        )
        self.wall_seconds, self.started = wall_seconds, time.monotonic()
        self.seed, self.temperature, self.top_p = seed, temperature, top_p
        self.usage = {
            "model_requests": 0,
            "generated_tokens": 0,
            "prompt_tokens": 0,
            "tool_calls": 0,
            "unknown_generation_reservation": 0,
        }
        self.events, self.stopped, self.termination = [], False, None
        if self.answer_guard:
            self.usage.update(tool_requests=0, harness_rejections=0)

    def build_request(self):
        if self.stopped:
            raise ValueError("Student session has already terminated")
        if (
            self.usage["model_requests"] >= self.max_requests
            or self.usage["generated_tokens"] >= self.max_generated_tokens
            or time.monotonic() - self.started >= self.wall_seconds
        ):
            self.stopped, self.termination = True, "budget_exhausted"
            raise ValueError("Student resource budget exhausted")
        validate_history(self.messages)
        text = self.tokenizer.apply_chat_template(
            self.messages,
            tools=self.tools,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        ids = self.tokenizer(text, add_special_tokens=False)["input_ids"]
        if len(ids) > self.max_input_tokens:
            self.stopped, self.termination = True, "context_budget_exhausted"
            raise ValueError("Student context too long; no silent truncation")
        return {
            "format": "qwen3-batch-generation-v1",
            "policy": self.identity,
            "messages": copy.deepcopy(self.messages),
            "tools": copy.deepcopy(self.tools),
            "input_token_ids": ids,
            "max_new_tokens": min(
                self.max_new_tokens,
                self.max_generated_tokens - self.usage["generated_tokens"],
            ),
            "seed": self.seed + self.usage["model_requests"],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "eos_token_id": self.tokenizer.eos_token_id,
        }

    def step(self, generate):
        request = self.build_request()
        number = self.usage["model_requests"] + 1
        name = f"{number:03d}"
        if self.output:
            write_json(self.output / "requests" / (name + ".request.json"), request)
        self.usage["model_requests"] += 1
        self.usage["prompt_tokens"] += len(request["input_token_ids"])
        event = {
            "request_id": name,
            "assistant_index": len(self.messages),
            "request_hash": digest(request),
            "tools": copy.deepcopy(self.tools),
            "history_hash": digest(self.messages),
            "dispatched_count": 0,
        }
        self.events.append(event)
        dispatch_started, response_received = False, False
        try:
            self.usage["unknown_generation_reservation"] = request["max_new_tokens"]
            response = generate(copy.deepcopy(request))
            response_received = True
            if self.output:
                write_json(
                    self.output / "requests" / (name + ".response.json"), response
                )
            event["response_hash"] = digest(response)
            ids = response["token_ids"]
            if not isinstance(ids, list) or any(
                type(i) is not int or i < 0 for i in ids
            ):
                raise ValueError("Missing actual generated token IDs")
            self.usage["generated_tokens"] += len(ids)
            self.usage["unknown_generation_reservation"] = 0
            if (
                response["policy"] != self.identity
                or response["input_token_ids"] != request["input_token_ids"]
                or not ids
                or len(ids) > request["max_new_tokens"]
                or ids[-1] != self.tokenizer.eos_token_id
            ):
                raise ValueError(
                    "Student identity, input, or generation boundary mismatch"
                )
            text = self.tokenizer.decode(ids, skip_special_tokens=False)
            message = parse_batch(text, "student_" + name, response["finish_reason"])
            calls = message.get("tool_calls", [])
            validate_history(
                self.messages
                + [message]
                + [
                    {"role": "tool", "tool_call_id": c["id"], "content": ""}
                    for c in calls
                ]
            )
            # Whole-batch validation uses ONLY the schemas visible before generation.
            for offset, call in enumerate(calls):
                f = call["function"]
                call_code(f["name"], f["arguments"], request["tools"])
                if (
                    f["name"] == "supervisor__complete_task"
                    and offset != len(calls) - 1
                ):
                    raise ValueError("Completion must be last")
            self.messages.append(message)
            guard = self.answer_guard
            verdicts = guard.inspect(calls) if guard else [None] * len(calls)
            if guard:
                event.update(
                    harness_verdicts=verdicts,
                    supervise=not any(verdicts),
                    appworld_dispatched_count=0,
                )
            for call, verdict in zip(calls, verdicts):
                if time.monotonic() - self.started >= self.wall_seconds:
                    raise TimeoutError("Batch interrupted by wall-clock budget")
                dispatch_started = True
                f = call["function"]
                if guard:
                    self.usage["tool_requests"] += 1
                if verdict is None:
                    self.usage["tool_calls"] += 1
                    result, self.tools = execute_with_mode(
                        self.adapter,
                        f["name"],
                        f["arguments"],
                        self.tools,
                        self.tool_mode,
                    )
                    if guard:
                        event["appworld_dispatched_count"] += 1
                else:
                    result = canonical(verdict)
                    self.usage["harness_rejections"] += 1
                observation = {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": f["name"],
                    "content": result,
                }
                self.messages.append(observation)
                event["dispatched_count"] += 1
                if self.output:
                    write_json(
                        self.output
                        / ("dispatches" if verdict is None else "harness_feedback")
                        / f"{name}_{event['dispatched_count']:03d}.json",
                        observation,
                    )
            validate_history(self.messages)
            event["status"] = "complete_boundary"
            if guard:
                event["answer_guard"] = guard.summary()
            if guard and guard.stopped:
                self.stopped, self.termination = True, "answer_correction_exhausted"
            elif not calls or self.adapter.world.task_completed():
                self.stopped = True
                self.termination = (
                    "completion_claim" if calls else "assistant_stop_without_tool"
                )
        except Exception as exc:
            self.stopped = True
            self.termination = (
                "infrastructure_error"
                if not response_received
                else "interrupted_batch"
                if dispatch_started
                else "rejected_generation"
            )
            event.update(status=self.termination, error_type=type(exc).__name__)
            raise
        finally:
            if self.output:
                write_json(
                    self.output / "turns" / (name + ".json"),
                    {
                        "event": event,
                        "messages": self.messages,
                        "usage": self.usage,
                        "termination": self.termination,
                        "actor_role": "student",
                        "rl_eligible": False,
                    },
                )
        return event

    def run(self, generate):
        while not self.stopped:
            try:
                self.step(generate)
            except ValueError:
                if self.termination not in {
                    "budget_exhausted",
                    "context_budget_exhausted",
                }:
                    raise
        return {
            "actor_role": "student",
            "termination": self.termination,
            "usage": self.usage,
            "rl_eligible": False,
            "training_started": False,
            "policy": self.identity,
        }
