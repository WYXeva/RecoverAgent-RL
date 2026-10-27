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
from .harness import (
    ProtocolHarness,
    capture_interruption,
    record_call_intent,
    record_tool_result,
    failure_category,
    json_object,
    check_batch,
    RULES as HARNESS_RULES,
    REFACTOR_REVISION,
)


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
    decoder = json.JSONDecoder(object_pairs_hook=json_object, parse_constant=_constant)
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
        protocol_harness=False,
        harness_revision=REFACTOR_REVISION,
        clock=None,
        memory_config=None,
        memory_state=None,
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
        if protocol_harness and max_input_tokens > 32768:
            raise ValueError("harness_v1 Student window cannot exceed 32768")
        validate_history(messages)
        public = {d["function"]["name"]: d for d in adapter.catalogue}
        if any(
            t != public.get(t["function"]["name"])
            or t["function"]["name"].startswith("admin__")
            for t in tools
        ):
            raise ValueError("Non-public or altered initial Student schema")
        self.adapter, self.tokenizer = adapter, tokenizer
        self.harness = (
            ProtocolHarness(tool_mode, revision=harness_revision)
            if protocol_harness
            else None
        )
        if self.harness and (
            messages[0]["role"] != "system"
            or HARNESS_RULES not in messages[0]["content"]
        ):
            raise ValueError("Student protocol harness must be declared in the prompt")
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
        self.clock = clock or time.monotonic
        self.wall_seconds, self.started = wall_seconds, self.clock()
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
        if self.harness:
            self.usage.update(protocol_rejections=0, harness_notices=0)
        self.memory = None
        if memory_config is not None or memory_state is not None:
            from .task_memory import TaskMemory, memory_config as normalize_memory

            config = normalize_memory(memory_config)
            if memory_state is not None and not config["enabled"]:
                raise ValueError("Cannot restore Memory into a disabled session")
            if config["enabled"]:
                self.memory = memory_state or TaskMemory(
                    adapter.task_id, messages, config
                )
                if (
                    self.memory.task_id != adapter.task_id
                    or self.memory.config != config
                ):
                    raise ValueError("Restored Memory task/config differs")
                self.memory.validate_boundary(messages, tools)
                self.usage.update(
                    memory_update_seconds=0.0,
                    context_compile_seconds=0.0,
                    uncompressed_prompt_tokens=0,
                    compressed_requests=0,
                )

    def build_request(self):
        if self.stopped:
            raise ValueError("Student session has already terminated")
        if (
            self.usage["model_requests"] >= self.max_requests
            or self.usage["generated_tokens"] >= self.max_generated_tokens
            or self.clock() - self.started >= self.wall_seconds
        ):
            self.stopped, self.termination = True, "budget_exhausted"
            raise ValueError("Student resource budget exhausted")
        if self.harness and self.adapter.world.task_completed():
            self.stopped, self.termination = True, "completion_claim"
            raise ValueError("AppWorld task already completed")
        validate_history(self.messages)
        compiled_messages, compilation = self.messages, None
        if self.memory is not None:
            from .context_compiler import ContextBudgetError, compile_context

            started = self.clock()
            try:
                compiled_messages, ids, compilation = compile_context(
                    self.tokenizer,
                    self.messages,
                    self.tools,
                    self.memory,
                    max_tokens=self.max_input_tokens,
                    reserve_tokens=min(
                        self.max_new_tokens,
                        self.max_generated_tokens - self.usage["generated_tokens"],
                    ),
                )
            except ContextBudgetError as exc:
                self.stopped, self.termination = True, "context_budget_exhausted"
                if self.output:
                    write_json(
                        self.output / "context_compilation_failure.json",
                        {
                            "reason": str(exc),
                            "history_sha256": digest(self.messages),
                            "memory_identity": self.memory.identity,
                            "no_truncation": True,
                        },
                    )
                raise
            finally:
                self.usage["context_compile_seconds"] += self.clock() - started
        else:
            text = self.tokenizer.apply_chat_template(
                self.messages,
                tools=self.tools,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            ids = self.tokenizer(text, add_special_tokens=False)["input_ids"]
        if len(ids) >= self.max_input_tokens:
            self.stopped, self.termination = True, "context_budget_exhausted"
            raise ValueError("Student context too long; no silent truncation")
        return {
            "format": "qwen3-batch-generation-v1",
            "policy": self.identity,
            "messages": copy.deepcopy(compiled_messages),
            "tools": copy.deepcopy(self.tools),
            "input_token_ids": ids,
            "max_new_tokens": min(
                self.max_new_tokens,
                self.max_generated_tokens - self.usage["generated_tokens"],
                self.max_input_tokens - len(ids),
            ),
            "seed": self.seed + self.usage["model_requests"],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "eos_token_id": self.tokenizer.eos_token_id,
            **({"memory_compilation": compilation} if compilation is not None else {}),
        }

    def step(self, generate):
        request = self.build_request()
        number = self.usage["model_requests"] + 1
        name = f"{number:03d}"
        if self.output:
            if self.memory is not None:
                write_json(
                    self.output / "requests" / (name + ".history.json"),
                    {
                        "messages": self.messages,
                        "tools": self.tools,
                        "memory_identity": self.memory.identity,
                    },
                )
            write_json(self.output / "requests" / (name + ".request.json"), request)
        self.usage["model_requests"] += 1
        self.usage["prompt_tokens"] += len(request["input_token_ids"])
        if self.memory is not None:
            self.usage["uncompressed_prompt_tokens"] += request["memory_compilation"][
                "original_tokens"
            ]
            self.usage["compressed_requests"] += int(
                request["memory_compilation"]["compressed"]
            )
        event = {
            "request_id": name,
            "assistant_index": len(self.messages),
            "request_hash": digest(request),
            "tools": copy.deepcopy(self.tools),
            "history_hash": digest(self.messages),
            "dispatched_count": 0,
        }
        self.events.append(event)
        batch_open, response_received = False, False
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
            if (
                response["policy"] != self.identity
                or response["input_token_ids"] != request["input_token_ids"]
                or not ids
                or len(ids) > request["max_new_tokens"]
            ):
                raise ValueError(
                    "Student identity, input, or generation boundary mismatch"
                )
            self.usage["generated_tokens"] += len(ids)
            self.usage["unknown_generation_reservation"] = 0
            text = self.tokenizer.decode(ids, skip_special_tokens=False)
            if not self.harness and ids[-1] != self.tokenizer.eos_token_id:
                raise ValueError("Missing Student EOS boundary")
            try:
                if (
                    ids[-1] != self.tokenizer.eos_token_id
                    and response["finish_reason"] != "length"
                ):
                    raise ValueError("Missing Student EOS boundary")
                message = parse_batch(
                    text, "student_" + name, response["finish_reason"]
                )
                if self.harness:
                    check_batch(
                        message, self.messages, request["tools"], require_action=True
                    )
            except (ValueError, KeyError, TypeError) as exc:
                if not self.harness:
                    raise
                event.update(
                    self.harness.rejection_event(
                        exc,
                        truncated=response["finish_reason"] == "length",
                        guard=self.answer_guard,
                    )
                )
                self.messages.append(event["feedback"])
                self.usage["protocol_rejections"] += 1
                event["status"] = "protocol_rejected"
                if self.harness.stopped:
                    self.stopped, self.termination = (
                        True,
                        "protocol_correction_exhausted",
                    )
                return event
            calls = message.get("tool_calls", [])
            if not self.harness:
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
            batch_open = bool(calls)
            event["tool_results"] = []
            for call, verdict in zip(calls, verdicts):
                if self.clock() - self.started >= self.wall_seconds:
                    raise TimeoutError("Batch interrupted by wall-clock budget")
                f = call["function"]
                if guard:
                    self.usage["tool_requests"] += 1
                if verdict is None:
                    if self.harness:
                        record_call_intent(self.output, event, call)
                    self.usage["tool_calls"] += 1
                    result, self.tools = execute_with_mode(
                        self.adapter,
                        f["name"],
                        f["arguments"],
                        self.tools,
                        self.tool_mode,
                    )
                else:
                    result = canonical(verdict)
                    self.usage["harness_rejections"] += 1
                record_tool_result(
                    self.output,
                    event,
                    self.messages,
                    call,
                    result,
                    executed=verdict is None,
                )
            validate_history(self.messages)
            batch_open = False
            if (
                self.harness
                and calls
                and not any(verdicts)
                and not any(
                    c["function"]["name"] == "supervisor__complete_task" for c in calls
                )
            ):
                notice = self.harness.notice(self.messages)
                if notice:
                    event["harness_notice"] = notice
                    self.messages.append(notice)
                    self.usage["harness_notices"] += 1
            event["status"] = "complete_boundary"
            if self.memory is not None and calls:
                started = self.clock()
                self.memory.observe(self.messages, event)
                self.usage["memory_update_seconds"] += self.clock() - started
                event["memory_identity"] = self.memory.identity
            if self.harness:
                event["failure_category"] = failure_category(event)
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
                if batch_open
                else "rejected_generation"
            )
            event.update(status=self.termination, error_type=type(exc).__name__)
            if self.harness:
                event["failure_category"] = failure_category(event)
            if self.harness and batch_open:
                event["interruption"] = capture_interruption(
                    self.adapter, self.output, self.messages, self.tools, event
                )
            raise
        finally:
            if self.harness:
                event["harness"] = self.harness.summary()
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
                    "completion_claim",
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
