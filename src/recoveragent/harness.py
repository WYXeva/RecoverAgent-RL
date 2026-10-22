"""Versioned public-evidence checks shared by Teacher and Qwen Student.

Rejected raw generations remain in controller receipts. They are never repaired,
executed, or represented as fabricated Assistant/tool messages. Only a declared
harness user message is added to the legal conversation for the next generation.
"""

import json
import re

from jsonschema import ValidationError

from .adapter import call_code
from .io import canonical, digest
from .teacher_history import validate_history

PROTOCOL = "public_protocol_correction_v1"
RULES = """PUBLIC HARNESS PROTOCOL v1
The harness checks complete responses before executing any call in the batch.
On harness_protocol_error the ENTIRE batch was NOT executed. Raw invalid output
is kept in an audit receipt, not repaired or inserted as a valid tool invocation.
Read the explicit feedback and generate a new complete response. At most two
correction opportunities per episode; all existing budgets still apply. Split
large batches across responses when output was truncated (4096 tokens maximum).
Copy authentication tokens exactly from the same application's observed login
result; never predict a token or use a login result before it has been returned.
In dynamic_discovery, obtain missing schemas using the visible discovery tools
in a separate response. In api_predictor, the initial execution tool set is fixed;
use only that set. The harness never adds tools, logs in, or changes arguments.
Public no-progress notices ask you to reconsider observed evidence, not assume
task completion. Preserve query/filter/sort conditions when paging. An empty
page is evidence about that query only. Verify requested sets and write scope.
The separate numeric-answer rule still allows only the immediately next reply.
"""

MESSAGES = {
    "truncated_output": "Generate a smaller complete batch. No prefix of the truncated response was executed.",
    "malformed_output": "Generate a complete non-thinking function-call response with valid JSON and unique call IDs.",
    "invalid_history": "Use fresh unique call IDs and a complete batch. No calls were executed.",
    "invisible_tool": "A requested function is not in the current tools schema. Follow your mode's tool acquisition rules before submitting it.",
    "invalid_arguments": "Check argument names, types and required fields against the currently visible schema.",
    "completion_order": "Place complete_task last, after all required observations are already available.",
    "unobserved_access_token": "An access_token does not match a prior login result from that application. Re-read the actual result; do not guess or alter tokens.",
}


class ProtocolViolation(ValueError):
    def __init__(self, code, tool_name=None):
        if code not in MESSAGES:
            raise ValueError("Unknown public protocol error")
        self.code = code
        self.tool_name = (
            tool_name
            if isinstance(tool_name, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,80}__[a-z][a-z0-9_]{0,80}", tool_name)
            else None
        )
        super().__init__(code)


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def strict_arguments(text):
    value = json.loads(text, object_pairs_hook=json_object)
    if not isinstance(value, dict):
        raise ValueError("Arguments must be an object")
    canonical(value)
    return value


def observed_tokens(messages):
    tokens = {}
    for m in messages:
        if m.get("role") != "tool" or not m.get("name", "").endswith("__login"):
            continue
        try:
            value = json.loads(m["content"])
        except (ValueError, TypeError):
            continue
        token = value.get("access_token") if isinstance(value, dict) else None
        if isinstance(token, str) and token:
            tokens.setdefault(m["name"].split("__")[0], set()).add(token)
    return tokens


def check_batch(message, messages, tools):
    calls = message.get("tool_calls", [])
    try:
        validate_history(
            messages
            + [message]
            + [{"role": "tool", "tool_call_id": c["id"], "content": ""} for c in calls]
        )
    except (ValueError, KeyError, TypeError):
        raise ProtocolViolation("invalid_history") from None
    visible = {t["function"]["name"] for t in tools}
    tokens = observed_tokens(messages)
    for offset, call in enumerate(calls):
        f = call["function"]
        if f["name"] not in visible:
            raise ProtocolViolation("invisible_tool", f["name"])
        try:
            args = strict_arguments(f["arguments"])
            call_code(f["name"], args, tools)
        except (ValueError, TypeError, ValidationError):
            raise ProtocolViolation("invalid_arguments", f["name"]) from None
        if f["name"] == "supervisor__complete_task" and offset != len(calls) - 1:
            raise ProtocolViolation("completion_order", f["name"])
        app = f["name"].split("__")[0]
        # Only make this check when a same-app login token has actually been seen.
        # Without that evidence, the real API remains the authority.
        if (
            "access_token" in args
            and tokens.get(app)
            and args["access_token"] not in tokens[app]
        ):
            raise ProtocolViolation("unobserved_access_token", f["name"])


class ProtocolHarness:
    def __init__(self, tool_mode):
        if tool_mode not in {"dynamic_discovery", "api_predictor"}:
            raise ValueError("Unknown tool mode")
        self.tool_mode = tool_mode
        self.rejections = 0
        self.notices = 0
        self.stopped = False

    def reject(self, code, *, answer_pending=False, tool_name=None):
        if self.stopped:
            raise ValueError("Protocol correction already exhausted")
        self.rejections += 1
        self.stopped = self.rejections > 2 or answer_pending
        feedback = {
            "source": "recoveragent_harness",
            "protocol": PROTOCOL,
            "error": "harness_protocol_error",
            "code": code,
            "appworld_executed": False,
            "whole_batch_rejected": True,
            "retry_allowed": not self.stopped,
            "tool_mode": self.tool_mode,
            "message": MESSAGES[code],
            "corrections_remaining": max(0, 3 - self.rejections)
            if not self.stopped
            else 0,
        }
        if tool_name is not None:
            feedback["function"] = tool_name
        return {"role": "user", "content": canonical(feedback)}

    def notice(self, messages):
        """Conservative consecutive-result warning; never suppress a real result.

        Two empty pages must be adjacent calls of the SAME function/query/filter
        with nondecreasing page indexes. Any intervening call resets the detector.
        Unknown response shapes do not count as empty. Warnings are advisory.
        """
        if self.notices >= 2:
            return None
        pairs, pending = [], {}
        for m in messages:
            for c in m.get("tool_calls", []):
                pending[c["id"]] = c["function"]
            if m["role"] == "tool" and m["tool_call_id"] in pending:
                pairs.append((pending[m["tool_call_id"]], m["content"]))
        if len(pairs) < 2:
            return None
        (a, ar), (b, br) = pairs[-2:]
        code = None
        if a == b and ar == br:
            code = "repeated_identical_observation"
        if a["name"] == b["name"]:
            if all(re.search(r"Response status code is 401\b", r) for r in (ar, br)):
                code = "repeated_authentication_failure"
            aa, ba = strict_arguments(a["arguments"]), strict_arguments(b["arguments"])
            ap, bp = aa.pop("page_index", None), ba.pop("page_index", None)
            if aa == ba and type(ap) is int and type(bp) is int and bp >= ap:
                try:
                    if json.loads(ar) == [] and json.loads(br) == []:
                        code = "repeated_empty_pages_same_query"
                except (ValueError, TypeError):
                    pass
        if code is None:
            return None
        self.notices += 1
        return {
            "role": "user",
            "content": canonical(
                {
                    "source": "recoveragent_harness",
                    "protocol": PROTOCOL,
                    "notice": code,
                    "message": "Repeated observations suggest no progress. Re-check the actual results, authentication and query/pagination conditions before choosing your next action. This does not establish task completion.",
                }
            ),
        }

    def summary(self):
        return {
            "protocol": PROTOCOL,
            "rejections": self.rejections,
            "notices": self.notices,
            "stopped": self.stopped,
        }


class ServiceCircuitBreaker:
    """Stop on provider auth/funding, or two consecutive unknown service failures.

    No implicit retry: each failed request keeps its unknown usage reservation.
    The batch stop is durable and propagated to the campaign controller.
    """

    def __init__(self):
        self.consecutive = 0
        self.reason = None

    def observe(self, trajectory):
        error = trajectory.get("provider_error", {})
        if trajectory.get("termination_reason") == "evaluation_error":
            self.reason = "evaluation_runtime_error"
        if error.get("http_status") in {401, 402, 403}:
            self.reason = "provider_global_block"
        if error or trajectory.get("termination_reason") == "worker_interrupted":
            self.consecutive += 1
        else:
            self.consecutive = 0
        if self.consecutive >= 2 and self.reason is None:
            self.reason = "consecutive_service_failures"
        return self.reason is not None


def provider_error_record(exc):
    # Frozen TeacherPolicy already sanitizes HTTP exceptions to this exact form.
    # Never serialize arbitrary exception text or a provider response body.
    match = re.fullmatch(r"Teacher HTTP ([0-9]{3}); usage unknown", str(exc))
    return {
        "type": "TeacherTransportError",
        "http_status": int(match[1]) if match else None,
        "kind": "http_error" if match else "transport_error",
        "usage_known": False,
    }


def validate_record(record):
    """Replay feedback deterministically using ONLY earlier visible history."""
    from .answer_guard import AnswerGuard

    if "harness" not in record:
        if any(
            "protocol_rejection" in t or "harness_notice" in t for t in record["turns"]
        ):
            raise ValueError("Undeclared protocol harness")
        return
    messages = record["messages"]
    if _harness_missing(record):
        raise ValueError("Harness prompt or version mismatch")
    h = ProtocolHarness(record["tool_mode"])
    guard = AnswerGuard(json.loads(messages[1]["content"])["instruction"])
    cursor = 2
    for t in record["turns"]:
        if h.stopped or guard.stopped:
            raise ValueError("Recorded generation after correction exhaustion")
        if t["assistant_index"] != cursor or t["history_hash"] != digest(
            messages[:cursor]
        ):
            raise ValueError("Harness history coverage mismatch")
        if "protocol_rejection" in t:
            feedback = h.reject(
                t["protocol_rejection"],
                answer_pending=guard.pending,
                tool_name=t.get("rejected_function"),
            )
            if guard.pending:
                guard.inspect([])
            if (
                t.get("supervise") is not False
                or t.get("appworld_dispatched_count") != 0
                or t.get("feedback") != feedback
                or messages[cursor] != feedback
            ):
                raise ValueError(
                    "Rejected raw output must be unsupervised and unexecuted"
                )
            cursor += 1
        else:
            check_batch(messages[cursor], messages[:cursor], t["tools"])
            calls = messages[cursor].get("tool_calls", [])
            verdicts = guard.inspect(calls)
            cursor += 1 + len(calls)
            # No notice is emitted on completion or on a numeric rejection.
            completion = any(
                c["function"]["name"] == "supervisor__complete_task" for c in calls
            )
            notice = (
                h.notice(messages[:cursor])
                if calls and not any(verdicts) and not completion
                else None
            )
            if t.get("harness_notice") != notice:
                raise ValueError("No-progress notice cannot be reproduced")
            if notice:
                if messages[cursor] != notice:
                    raise ValueError("No-progress feedback mismatch")
                cursor += 1
    if cursor != len(messages) or record["harness"] != h.summary():
        raise ValueError("Harness state or conversation coverage mismatch")


def _harness_missing(record):
    return (
        record["harness"].get("protocol") != PROTOCOL
        or RULES not in record["messages"][0]["content"]
    )
