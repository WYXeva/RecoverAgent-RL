"""Explicitly authorized, native structured Teacher function calling (no SDK retries)."""

import copy
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .io import canonical
from .teacher_history import validate_history


class TeacherBlocked(ValueError):
    pass


class TeacherTransportError(RuntimeError):
    """Response/usage is unknown. Do not retry or silently assign zero cost."""


def read_credentials(path=None):
    """Read a small dotenv subset as data, never shell code; environment wins."""
    values = {}
    if path:
        for line in Path(path).read_text().splitlines():
            match = re.fullmatch(
                r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line.strip()
            )
            if match:
                value = match[2].strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[match[1]] = value
    values.update(os.environ)
    return values


def preflight(config, credentials):
    teacher, approval = config["teacher"], config["authorization"]
    reasons = []
    if not approval.get("approved") or not approval.get("reference"):
        reasons.append("PAID_TEACHER_NOT_AUTHORIZED")
    if not credentials.get(teacher["api_key_env"]):
        reasons.append("TEACHER_CREDENTIAL_MISSING")
    if not teacher.get("model") or not teacher.get("revision"):
        reasons.append("TEACHER_IDENTITY_MISSING")
    if teacher.get("model") in {"Qwen/Qwen3-4B", "Qwen/Qwen3-1.7B"}:
        reasons.append("STUDENT_CANNOT_SUBSTITUTE_FOR_TEACHER")
    rates = teacher.get("pricing", {})
    for name in ("input_per_million", "cached_input_per_million", "output_per_million"):
        if (
            type(rates.get(name)) not in (int, float)
            or not math.isfinite(rates[name])
            or rates[name] < 0
        ):
            reasons.append("VERIFIED_PRICE_REQUIRED")
            break
    if not rates.get("source") or not rates.get("checked_at"):
        reasons.append("PRICE_SOURCE_REQUIRED")
    if approval.get("currency") != rates.get("currency"):
        reasons.append("PRICE_CURRENCY_MISMATCH")
    if (
        type(approval.get("max_cost")) not in (int, float)
        or not math.isfinite(approval["max_cost"])
        or approval["max_cost"] <= 0
    ):
        reasons.append("COST_LIMIT_REQUIRED")
    url = urllib.parse.urlsplit(teacher["base_url"])
    if (
        url.scheme != "https"
        or url.hostname != teacher["allowed_host"]
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        reasons.append("INVALID_TEACHER_ENDPOINT")
    if teacher.get("thinking") is not False:
        reasons.append("P3A_V1_REQUIRES_NON_THINKING_PROTOCOL")
    return {
        "status": "BLOCKED" if reasons else "READY_FOR_PILOT",
        "reasons": sorted(set(reasons)),
        "credential_present": bool(credentials.get(teacher["api_key_env"])),
        "model": teacher["model"],
        "revision": teacher["revision"],
        "revision_is_immutable": teacher["revision_is_immutable"],
        "capability": "UNVERIFIED_UNTIL_OFFICIAL_TASK_SUCCESS",
        "provider_requests": 0,
    }


def normalize_message(response, max_calls=1):
    """Preserve native batches; reject malformed/oversized batches atomically."""
    choices = response.get("choices", [])
    if len(choices) != 1:
        raise ValueError("Expected one choice")
    choice = choices[0]
    if choice.get("finish_reason") not in {"stop", "tool_calls"}:
        raise ValueError("Truncated, filtered or unsupported completion")
    raw = choice["message"]
    if raw.get("role") != "assistant" or raw.get("reasoning_content"):
        raise ValueError("Unexpected role or reasoning in non-thinking protocol")
    content = raw.get("content")
    if content is not None and not isinstance(content, str):
        raise ValueError("Only text assistant content is supported")
    message = {"role": "assistant", "content": content or ""}
    calls = raw.get("tool_calls") or []
    if calls:
        if max_calls is not None and len(calls) > max_calls:
            raise ValueError("Tool batch exceeds configured limit")
        identities = set()
        for call in calls:
            if (
                call.get("type") != "function"
                or not isinstance(call.get("id"), str)
                or not call["id"]
                or call["id"] in identities
            ):
                raise ValueError("Missing or duplicate function call ID/type")
            identities.add(call["id"])
            fn = call["function"]
            if not isinstance(fn.get("name"), str) or not isinstance(
                fn.get("arguments"), str
            ):
                raise ValueError(
                    "Expected native function name and JSON argument string"
                )
            arguments = json.loads(fn["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("Function arguments must be an object")
            canonical(arguments)
        message["tool_calls"] = copy.deepcopy(calls)
    elif not message["content"]:
        raise ValueError("Empty assistant completion")
    return message


def response_usage(response):
    usage = response["usage"]
    prompt, output = usage["prompt_tokens"], usage["completion_tokens"]
    cached = usage.get(
        "prompt_cache_hit_tokens",
        usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
    )
    if (
        any(type(x) is not int or x < 0 for x in (prompt, output, cached))
        or cached > prompt
    ):
        raise ValueError("Invalid provider token usage")
    return {
        "prompt_tokens": prompt,
        "generated_tokens": output,
        "cached_prompt_tokens": cached,
    }


def usage_cost(usage, pricing):
    """Usage-priced estimate; not a provider invoice or measured account debit."""
    cached = usage["cached_prompt_tokens"]
    return (
        (usage["prompt_tokens"] - cached) * pricing["input_per_million"]
        + cached * pricing["cached_input_per_million"]
        + usage["generated_tokens"] * pricing["output_per_million"]
    ) / 1_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a credential to a redirected endpoint.


class TeacherPolicy:
    def __init__(self, config, credentials, transport=None):
        check = preflight(config, credentials)
        if check["status"] != "READY_FOR_PILOT":
            raise TeacherBlocked(",".join(check["reasons"]))
        self.config = config["teacher"]
        self.key = credentials[self.config["api_key_env"]]
        self.transport = transport or self._post

    def _post(self, payload):
        request = urllib.request.Request(
            self.config["base_url"].rstrip("/") + "/chat/completions",
            canonical(payload).encode(),
            {"Content-Type": "application/json", "Authorization": "Bearer " + self.key},
        )
        try:
            with urllib.request.build_opener(NoRedirect).open(
                request, timeout=self.config["timeout_seconds"]
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Provider bodies may contain request text or credentials. Retain status only.
            raise TeacherTransportError(
                f"Teacher HTTP {exc.code}; usage unknown"
            ) from None
        except Exception as exc:
            raise TeacherTransportError(
                f"Teacher {type(exc).__name__}; usage unknown"
            ) from None

    def build_request(self, messages, tools, max_tokens):
        validate_history(messages)
        payload = {
            "model": self.config["model"],
            "messages": copy.deepcopy(messages),
            "tools": copy.deepcopy(tools),
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": self.config["temperature"],
            "thinking": {"type": "disabled"},
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }
        if len(canonical(payload).encode()) > self.config["max_request_bytes"]:
            raise TeacherBlocked("REQUEST_SIZE_LIMIT")
        return payload

    def build_text_request(self, messages, max_tokens):
        """Predictor uses upstream text messages; no Actor tools or hidden labels."""
        payload = {
            "model": self.config["model"],
            "messages": copy.deepcopy(messages),
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": self.config["temperature"],
            "thinking": {"type": "disabled"},
        }
        if len(canonical(payload).encode()) > self.config["max_request_bytes"]:
            raise TeacherBlocked("REQUEST_SIZE_LIMIT")
        return payload

    def generate(self, messages, tools, max_tokens):
        payload = self.build_request(messages, tools, max_tokens)
        return payload, self.transport(payload)
