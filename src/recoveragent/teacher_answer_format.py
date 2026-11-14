"""Versioned, public answer-format notice for fresh Teacher demonstrations only."""

import copy
import json

PROTOCOL = "public_numeric_answer_format_v1"
RULE = (
    "For a question requesting one numeric amount or count, submit the final "
    "answer to supervisor.complete_task as a JSON number, without currency symbols, "
    "units, thousands separators, explanatory prose, or markdown. Compute the "
    "value yourself from public observations. Preserve names, identifiers, exact "
    "quoted text, multiple values, percentages and any explicit formatting the "
    "user requested; do not turn arbitrary text into a number. This is an output "
    "format instruction, not evidence that an answer is correct."
)
NOTICE = {"revision": PROTOCOL, "rule": RULE}


def public_task(task, teacher):
    value = copy.deepcopy(task)
    protocol = teacher.get("answer_format_contract")
    if protocol is not None:
        if protocol != PROTOCOL:
            raise ValueError("Unknown Teacher answer format contract")
        value["answer_format_contract"] = copy.deepcopy(NOTICE)
    return value


def validate_record(record):
    task = json.loads(record["messages"][1]["content"])
    protocol = record.get("teacher", {}).get("answer_format_contract")
    expected = NOTICE if protocol == PROTOCOL else None
    if (
        protocol not in {None, PROTOCOL}
        or task.get("answer_format_contract") != expected
    ):
        raise ValueError(
            "Teacher public answer-format notice differs from its revision"
        )
