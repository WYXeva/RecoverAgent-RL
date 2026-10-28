"""Public, conservative numeric-answer checks; never repair or evaluate an answer."""

import json
import math
import re
from decimal import Decimal

from .io import canonical, digest

PROTOCOL = "numeric_answer_self_correction_v1"
COMPLETE = "supervisor__complete_task"

RULES = """FINAL ANSWER FORMAT CONTRACT
For a request for a single amount or count, use a JSON number in complete_task's
answer, without a currency symbol or unit. Names, identifiers, exact quoted text,
ranges, percentages and multi-value answers must retain their requested semantics;
do not turn arbitrary text into a number. Follow any explicit requested formatting.
The harness may check an unambiguous scalar contract inferred ONLY from the public
instruction. It does not know or check the correct answer. Before completing,
check your answer type. A harness_format_error means the completion was NOT sent
to AppWorld and has NOT ended the task. You have at most ONE further model reply,
within the existing resource budget, to submit a single complete_task call with
the SAME numeric value as a JSON number. No intervening tools or extra attempts.
The harness does not rewrite your answer. If blocked, use status=fail; do not
claim success without the necessary evidence.
"""


def public_contract(instruction):
    """Recognize only narrow scalar questions; everything else is unclassified.

    This is an observable heuristic, not a universal semantic task classifier.
    No task IDs, scenario IDs, ground truth, tool observations or evaluator inputs.
    """
    text = " ".join(instruction.lower().split())
    question, sep, _ = text.partition("?")
    excluded = (
        r"\b(each|respectively|list|names?|identifiers?|ids?|format(?:ted)?|"
        r"symbols?|currencies|currency|percent(?:age)?|between|range|"
        r"exact(?:ly)?|verbatim|quoted|round(?:ed)?|and|or)\b|[%\"']"
    )
    kind, rule = "unclassified", "no_unambiguous_scalar_question"
    if (
        sep
        and text.count("?") == 1
        and not re.search(excluded, question)
        and not re.search(r"\b(format(?:ted)?|symbol|currency|verbatim|quoted)\b", text)
    ):
        if re.match(
            r"^what is the (?:total|average) (?:cost|price|amount)\b", question
        ):
            kind, rule = "money", "single_total_or_average_money_question"
        elif re.match(r"^how many\b", question):
            kind, rule = "number", "single_count_question"
    return {"kind": kind, "rule": rule, "instruction_sha256": digest(instruction)}


def numeric_representation(value):
    """Parse only a whole, unambiguous scalar; NEVER return replacement arguments."""
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            return None
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*([$€£¥]?)\s*(-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?)\s*", value)
    return Decimal(match[2]) if match else None


class AnswerGuard:
    def __init__(self, instruction):
        self.contract = public_contract(instruction)
        self.pending = False
        self.stopped = False
        self.expected = None
        self.first_submission_valid = None
        self.correction_used = False
        self.correction_valid = None

    def inspect(self, calls):
        """Return one verdict per call; None means forward the exact original call.

        A retry consumes the next model response, even if it has no completion.
        The entire retry batch is rejected if it is not the promised single call.
        """
        if self.stopped:
            raise ValueError("Answer correction already exhausted")
        retry = self.pending
        if retry:
            self.pending = False
            self.correction_used = True
        verdicts = [None] * len(calls)
        if self.contract["kind"] == "unclassified":
            return verdicts
        if retry and (len(calls) != 1 or calls[0]["function"]["name"] != COMPLETE):
            self.stopped, self.correction_valid = True, False
            return [self._error("single_completion_required", False) for _ in calls]
        for i, call in enumerate(calls):
            function = call["function"]
            if function["name"] != COMPLETE:
                continue
            args = function["arguments"]
            args = json.loads(args) if isinstance(args, str) else args
            if args.get("status", "success") != "success":
                if retry:
                    self.correction_valid = False
                continue
            value = args.get("answer")
            number = numeric_representation(value)
            valid = type(value) in (int, float) and number is not None
            if self.first_submission_valid is None:
                self.first_submission_valid = valid
            if retry:
                valid = valid and number == self.expected
                self.correction_valid = valid
                if not valid:
                    self.stopped = True
                    verdicts[i] = self._error(
                        "correction_invalid_or_value_changed", False
                    )
            elif not valid and number is not None:
                # Only clear number/currency strings earn the format-only retry.
                self.expected, self.pending = number, True
                verdicts[i] = self._error("numeric_json_required", True)
            # Ambiguous representations are not converted, guessed, or retried.
        return verdicts

    def _error(self, code, retry_allowed):
        return {
            "source": "recoveragent_harness",
            "protocol": PROTOCOL,
            "error": "harness_format_error",
            "code": code,
            "appworld_executed": False,
            "retry_allowed": retry_allowed,
            "message": (
                "This completion was not executed. Keep the same numeric value and "
                "resubmit answer as a JSON number, without a currency symbol/unit. "
                "Your next model reply is the only correction opportunity and must "
                "contain exactly one complete_task call; existing budgets still apply."
                if retry_allowed
                else "The single correction opportunity is exhausted; no tools in this "
                "rejected correction were executed."
            ),
        }

    def summary(self):
        return {
            "protocol": PROTOCOL,
            "contract": self.contract,
            "first_submission_valid": self.first_submission_valid,
            "correction_used": self.correction_used,
            "correction_valid": self.correction_valid,
            "pending": self.pending,
            "stopped": self.stopped,
        }


def validate_guard_record(record):
    """Replay checks from public instruction + actual calls; no hidden evaluator."""
    if "answer_guard" not in record:
        if any("supervise" in turn for turn in record["turns"]):
            raise ValueError("Supervision override requires a declared answer guard")
        return
    messages = record["messages"]
    guard = AnswerGuard(json.loads(messages[1]["content"])["instruction"])
    if RULES not in messages[0]["content"]:
        if not record.get("custom_research"):
            raise ValueError("Undeclared answer guard")
        from .research_access import validate_record

        validate_record(record)  # Exact v6 plus real controls; replay below unchanged.
    for turn in record["turns"]:
        if "protocol_rejection" in turn and "harness" in record:
            if guard.pending:
                guard.inspect([])
            continue
        index = turn["assistant_index"]
        calls = messages[index].get("tool_calls", [])
        verdicts = guard.inspect(calls)
        if turn.get("harness_verdicts") != verdicts or turn.get("supervise") != (
            not any(verdicts)
        ):
            raise ValueError("Answer guard decision/supervision mismatch")
        if turn.get("appworld_dispatched_count") != sum(v is None for v in verdicts):
            raise ValueError("Answer guard dispatch accounting mismatch")
        for offset, verdict in enumerate(verdicts):
            if verdict is not None and messages[index + offset + 1][
                "content"
            ] != canonical(verdict):
                raise ValueError("Harness feedback mismatch")
    if guard.summary() != record["answer_guard"]:
        raise ValueError("Answer guard state mismatch")
