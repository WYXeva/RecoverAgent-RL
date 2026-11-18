"""Teacher diagnostic using Student's XML format/parser, without provider tools.

Provider reasoning and raw text remain in receipts. This is not Student token
sampling and cannot supply behavior probabilities for GRPO.
"""

import copy
import hashlib
import json

from .io import canonical, digest
from .teacher import TeacherPolicy, TeacherBlocked, normalize_message, reasoning_enabled
from .v2_model import parse_native

REVISION = "teacher_student_xml_probe_v2"


def text_feedback(message):
    """Describe the actual parser format; leave structural Harness receipts intact."""
    try:
        value = json.loads(message["content"])
    except (ValueError, TypeError):
        return message
    if not isinstance(value, dict) or value.get("error") != "harness_protocol_error":
        return message
    if value.get("code") in {"malformed_output", "unexpected_stop", "invalid_history"}:
        value["message"] = (
            "No tool was executed. Emit the XML Function Calling format from the "
            "system instructions in message.content: "
            "<tool_call><function=FUNCTION_NAME>"
            "<parameter=PARAMETER_NAME>VALUE</parameter>"
            "</function></tool_call>. Omit parameter blocks for a no-argument "
            "function. Use one complete tool_call block per call. Do not emit "
            "DSML tags, a JSON call envelope, or call IDs. Use only a startup "
            "function or one whose complete definition was returned earlier."
        )
    value["response_format"] = "qwen_xml"
    return {**message, "content": canonical(value)}


class TextFunctionTeacher(TeacherPolicy):
    def __init__(self, config, credentials, template, transport=None):
        super().__init__(config, credentials, transport=transport)
        self.identity = config["student"]
        if (
            self.identity["tool_format"] != "qwen_xml"
            or hashlib.sha256(template.encode()).hexdigest()
            != self.identity["chat_template_sha256"]
        ):
            raise ValueError("Teacher text probe requires the pinned Student template")
        self.template = template
        self.initial_tools = None
        self.system = None
        self.raw_by_index = {}
        self.rejected_by_index = {}

    def build_request(self, messages, tools, max_tokens):
        # First use the existing reasoning-history handling, with original indices.
        payload = super().build_request(messages, [], max_tokens)
        if self.initial_tools is None:
            from transformers.utils.chat_template_utils import _compile_jinja_template

            rendered = _compile_jinja_template(self.template).render(
                messages=messages[:2],
                tools=tools,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            prefix = "<|im_start|>system\n"
            if not rendered.startswith(prefix):
                raise ValueError("Student template has no expected system boundary")
            self.system = rendered[len(prefix) :].split("<|im_end|>", 1)[0]
            self.initial_tools = copy.deepcopy(tools)
        if tools != self.initial_tools:
            raise ValueError("Teacher text probe startup definitions changed")
        converted = []
        for index, message in enumerate(payload["messages"]):
            if index in self.rejected_by_index:
                # Invalid text is visible history, never a fabricated valid action.
                converted.append(copy.deepcopy(self.rejected_by_index[index]))
            m = copy.deepcopy(message)
            if m["role"] == "system":
                m["content"] = self.system
            elif m["role"] == "assistant":
                m = {
                    "role": "assistant",
                    "content": self.raw_by_index[index],
                    **(
                        {"reasoning_content": m["reasoning_content"]}
                        if "reasoning_content" in m
                        else {}
                    ),
                }
            elif m["role"] == "tool":
                content = "<tool_response>\n" + m["content"] + "\n</tool_response>"
                if index and payload["messages"][index - 1]["role"] == "tool":
                    converted[-1]["content"] += "\n" + content
                    continue
                m = {"role": "user", "content": content}
            elif m["role"] == "user":
                m = text_feedback(m)
            converted.append(m)
        payload["messages"] = converted
        for key in ("tools", "tool_choice", "parallel_tool_calls"):
            payload.pop(key, None)
        if len(canonical(payload).encode()) > self.config["max_request_bytes"]:
            raise TeacherBlocked("REQUEST_SIZE_LIMIT")
        return payload

    def parse(self, response, tools, request_id, guard):
        raw = normalize_message(
            response, None, allow_reasoning=reasoning_enabled(self.config)
        )
        if raw.get("tool_calls"):
            raise ValueError("Native tool_calls are not allowed in a text probe")
        message = parse_native(
            raw["content"],
            "teacher_text_" + request_id,
            "stop",
            tools,
            self.identity,
            answer_contract=guard.output_contract if guard else None,
            audit_conversion=bool(guard and guard.revision),
        )
        maximum = self.config.get("max_tool_calls_per_turn", 1)
        if maximum is not None and len(message.get("tool_calls", [])) > maximum:
            raise ValueError("Tool batch exceeds configured limit")
        return message

    def remember_response(self, index, response):
        super().remember_response(index, response)
        self.raw_by_index[index] = response["choices"][0]["message"]["content"]

    def remember_rejection(self, index, response):
        raw = response["choices"][0]["message"]
        self.rejected_by_index[index] = {
            "role": "assistant",
            "content": raw.get("content") or "",
            **(
                {"reasoning_content": raw["reasoning_content"]}
                if reasoning_enabled(self.config)
                and isinstance(raw.get("reasoning_content"), str)
                else {}
            ),
        }

    def binding(self):
        return {
            "revision": REVISION,
            "native_template_sha256": self.identity["chat_template_sha256"],
            "system_sha256": digest(self.system),
            "initial_tools_sha256": digest(self.initial_tools),
            "provider_tools": False,
            "behavior_logprobs": False,
            "rejected_text_preserved": True,
            "feedback_format": "qwen_xml",
        }
