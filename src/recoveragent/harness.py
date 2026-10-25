"""Versioned public-evidence checks shared by Teacher and Qwen Student.

Rejected raw generations remain in controller receipts. They are never repaired,
executed, or represented as fabricated Assistant/tool messages. Only a declared
harness user message is added to the legal conversation for the next generation.
"""

import copy
import json
import re
from pathlib import Path

from jsonschema import ValidationError

from .adapter import call_code
from .io import canonical, digest, files_manifest, write_json
from .teacher_history import validate_history

PROTOCOL = "public_protocol_correction_v1"
RUNTIME_REVISION = "review_20261023"
SCHEMA_REVISION = "schema_feedback_20261025"
REFACTOR_REVISION = "shared_execution_20261025"
SCHEMA_REVISIONS = {SCHEMA_REVISION, REFACTOR_REVISION}
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
    "unexpected_stop": "The task is still active. Continue using the visible tools, or report inability with supervisor__complete_task(status='fail'). Plain text does not end an AppWorld task. No tool was executed.",
    "truncated_output": "Generate a smaller complete batch. No prefix of the truncated response was executed.",
    "malformed_output": "Generate a complete non-thinking function-call response with valid JSON and unique call IDs.",
    "invalid_history": "Use fresh unique call IDs and a complete batch. No calls were executed.",
    "invisible_tool": "A requested function is not in the current tools schema. Follow your mode's tool acquisition rules before submitting it.",
    "invalid_arguments": "Check argument names, types and required fields against the currently visible schema.",
    "completion_order": "Place complete_task last, after all required observations are already available.",
    "unobserved_access_token": "An access_token does not match a prior login result from that application. Re-read the actual result; do not guess or alter tokens.",
}


class ProtocolViolation(ValueError):
    def __init__(self, code, tool_name=None, schema_error=None):
        if code not in MESSAGES:
            raise ValueError("Unknown public protocol error")
        self.code = code
        self.schema_error = schema_error
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


def schema_error_detail(error):
    """Schema-defined guidance without serializing offending values/tokens."""
    detail = {"path": list(error.absolute_path), "rule": error.validator}
    if error.validator == "required":
        detail["missing_fields"] = [
            k for k in error.validator_value if k not in error.instance
        ]
    elif error.validator == "additionalProperties":
        properties = error.schema.get("properties", {})
        patterns = error.schema.get("patternProperties", {})
        detail["unknown_fields"] = sorted(
            k
            for k in error.instance
            if k not in properties and not any(re.search(p, k) for p in patterns)
        )
        detail["allowed_fields"] = sorted(properties)
    elif error.validator == "type":
        detail["expected_type"] = error.validator_value
    return detail


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


def check_batch(message, messages, tools, *, require_action=False):
    calls = message.get("tool_calls", [])
    try:
        validate_history(
            messages
            + [message]
            + [{"role": "tool", "tool_call_id": c["id"], "content": ""} for c in calls]
        )
    except (ValueError, KeyError, TypeError):
        raise ProtocolViolation("invalid_history") from None
    if require_action and not calls:
        raise ProtocolViolation("unexpected_stop")
    visible = {t["function"]["name"] for t in tools}
    tokens = observed_tokens(messages)
    for offset, call in enumerate(calls):
        f = call["function"]
        if f["name"] not in visible:
            raise ProtocolViolation("invisible_tool", f["name"])
        try:
            args = strict_arguments(f["arguments"])
            call_code(f["name"], args, tools)
        except ValidationError as exc:
            raise ProtocolViolation(
                "invalid_arguments", f["name"], schema_error_detail(exc)
            ) from None
        except (ValueError, TypeError):
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
    def __init__(self, tool_mode, *, revision=REFACTOR_REVISION):
        if tool_mode not in {"dynamic_discovery", "api_predictor"}:
            raise ValueError("Unknown tool mode")
        self.tool_mode = tool_mode
        if revision not in {None, RUNTIME_REVISION, *SCHEMA_REVISIONS}:
            raise ValueError("Unknown harness runtime revision")
        self.revision = revision
        self.rejections = 0
        self.notices = 0
        self.stopped = False

    def reject(self, code, *, answer_pending=False, tool_name=None, schema_error=None):
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
        if self.revision in SCHEMA_REVISIONS and schema_error is not None:
            feedback["schema_error"] = schema_error
        return {"role": "user", "content": canonical(feedback)}

    def rejection_event(self, exc, *, truncated, guard):
        code, function = rejection_details(exc, truncated=truncated)
        pending = bool(guard and guard.pending)
        feedback = self.reject(
            code,
            answer_pending=pending,
            tool_name=function,
            schema_error=getattr(exc, "schema_error", None),
        )
        if pending:
            guard.inspect([])  # Never extend the one-response numeric budget.
        return {
            "protocol_rejection": code,
            "failure_category": "protocol",
            "rejected_function": function,
            "supervise": False,
            "feedback": feedback,
            "appworld_dispatched_count": 0,
            "dispatched_count": 0,
            "tool_results": [],
        }

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
            **({"runtime_revision": self.revision} if self.revision else {}),
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
        category = failure_category(trajectory)
        if category == "evaluation_runtime":
            self.reason = "evaluation_runtime_error"
        if error.get("http_status") in {401, 402, 403}:
            self.reason = "provider_global_block"
        if category in {"provider", "worker_environment"}:
            self.consecutive += 1
        else:
            self.consecutive = 0
        if self.consecutive >= 2 and self.reason is None:
            self.reason = "consecutive_service_failures"
        return self.reason is not None


def failure_category(trajectory):
    """Classify controller failures; NEVER infer a provider status from tool text."""
    if trajectory.get("provider_error"):
        return "provider"
    termination = trajectory.get("termination_reason", trajectory.get("status"))
    if termination == "evaluation_error":
        return "evaluation_runtime"
    if trajectory.get("status") == "infrastructure_error" or termination in {
        "worker_interrupted",
        "interrupted_batch",
        "rejected_generation",
        "infrastructure_error",
    }:
        return "worker_environment"
    if trajectory.get("status") == "valid" and trajectory.get("reward") == 0:
        return "task_failure"
    if "protocol_rejection" in trajectory or any(
        trajectory.get("harness_verdicts", [])
    ):
        return "protocol"
    if any(public_tool_error(r["content"]) for r in trajectory.get("tool_results", [])):
        return "business"
    return None


def public_tool_error(result):
    """Recognize AppWorld error envelopes, not error words inside ordinary data.

    A tool returning a business HTTP error remains a real observation. It is
    never a provider failure or permission to replay the request automatically.
    """
    return isinstance(result, str) and bool(
        re.match(
            r"^(?:Execution failed\.|Response status code is [45]\d\d\b)",
            result.lstrip(),
        )
    )


def rejection_details(exc, *, truncated):
    if isinstance(exc, ProtocolViolation):
        return exc.code, exc.tool_name
    return ("truncated_output" if truncated else "malformed_output"), None


def record_call_intent(directory, event, call):
    """Durable BEFORE dispatch, so a missing result is unknown, never zero calls."""
    event["inflight_call_id"] = call["id"]
    event["appworld_attempted_count"] = event.get("appworld_attempted_count", 0) + 1
    if directory is not None:
        write_json(
            Path(directory)
            / "call_intents"
            / f"{event['request_id']}_{event['appworld_attempted_count']:03d}.json",
            {
                "request_id": event["request_id"],
                "call": call,
                "history_hash": event["history_hash"],
                "tools_hash": digest(event["tools"]),
            },
        )


def record_tool_result(directory, event, messages, call, result, *, executed):
    """One ordered observation/receipt path shared by Teacher and Student.

    Called only after execute returns, or for explicitly declared guard feedback.
    On an exception before return the intent stays in flight and no result is
    invented. Keep history/event before disk IO so interruption capture retains
    known results even if persisting the individual receipt fails.
    """
    observation = {
        "role": "tool",
        "tool_call_id": call["id"],
        "name": call["function"]["name"],
        "content": result,
    }
    messages.append(observation)
    event.setdefault("tool_results", []).append(copy.deepcopy(observation))
    event["dispatched_count"] = event.get("dispatched_count", 0) + 1
    event["appworld_dispatched_count"] = event.get(
        "appworld_dispatched_count", 0
    ) + int(executed)
    if executed:
        event.pop("inflight_call_id", None)
    if directory is not None:
        write_json(
            Path(directory)
            / ("dispatches" if executed else "harness_feedback")
            / f"{event['request_id']}_{event['dispatched_count']:03d}.json",
            observation,
        )


def capture_interruption(adapter, directory, messages, tools, event):
    """Best-effort control evidence, NEVER a resumable complete-batch checkpoint.

    Keep the actual partial conversation and DB together. An in-flight call may
    have mutated state without returning; do not fabricate its missing result.
    Saving failure is explicit and must not mask the original execution error.
    """
    if directory is None:
        return {"status": "unavailable", "reason": "no_output_directory"}
    destination = Path(directory) / "interruption"
    result = {"status": "saved", "resume_eligible": False}
    try:
        validate_history(messages[: event["assistant_index"]])
        write_json(
            destination / "context.json",
            {"messages": messages, "tools": tools, "event": event},
        )
        adapter.export_state(
            "harness_interrupted_" + event["request_id"], destination / "dbs"
        )
        write_json(destination / "runtime.json", adapter.runtime())
        manifest = {
            "format": "harness-interruption-evidence-v1",
            "position": "interrupted_batch",
            "resume_eligible": False,
            "pre_batch_history_end": event["assistant_index"],
            "pre_batch_history_hash": digest(messages[: event["assistant_index"]]),
            "history_hash": digest(messages),
            "logical_state_hash": adapter.fingerprint(),
            "uncertain_call_id": event.get("inflight_call_id"),
            "files": files_manifest(destination),
        }
        write_json(destination / "manifest.json", manifest)
        result["manifest_hash"] = digest(manifest)
    except Exception as exc:
        result.update(status="save_error", error_type=type(exc).__name__)
    return result


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
    h = ProtocolHarness(
        record["tool_mode"], revision=record["harness"].get("runtime_revision")
    )
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
            if t["protocol_rejection"] == "unexpected_stop" and not h.revision:
                raise ValueError(
                    "Unexpected-stop correction requires the reviewed runtime"
                )
            feedback = h.reject(
                t["protocol_rejection"],
                answer_pending=guard.pending,
                tool_name=t.get("rejected_function"),
                # Details are part of visible harness feedback, not an Assistant
                # target. validate_receipts independently recomputes them from
                # the immutable provider response and this turn's schema.
                schema_error=json.loads(t["feedback"]["content"]).get("schema_error"),
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
            check_batch(
                messages[cursor],
                messages[:cursor],
                t["tools"],
                require_action=bool(h.revision),
            )
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
