"""One immutable native function, shared by Teacher and Student."""

import json
import time

from jsonschema import Draft202012Validator

from recoveragent.io import digest
from recoveragent.teacher_history import validate_history

REVISION = "codeact_reboot_v1"


class BudgetExhausted(ValueError):
    """A normal bounded attempt ending, not an environment/transport failure."""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "appworld",
            "description": "Execute Python in this task's persistent AppWorld environment. Returns printed observations or execution errors.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    }
]
SYSTEM = """Complete the user's AppWorld task through the appworld tool.
Call appworld with code: a Python string, without Markdown fences. Its Python
session persists throughout this task: reuse variables, use loops and conditions,
and batch related API operations in short purposeful code blocks. Call at most
one appworld tool per reply. Brief planning is enough; print useful observations,
not large unfiltered datasets. Check results before further state-changing actions.

Use public APIs as apis.<app>.<api>(...). Discover their documentation with:
print(apis.api_docs.show_app_descriptions())
print(apis.api_docs.show_api_descriptions(app_name='gmail'))
print(apis.api_docs.show_api_doc(app_name='gmail', api_name='login'))
Read the actual parameter definitions before using an unfamiliar API. Documentation
does not register or unlock tools: appworld remains the only tool for every turn.
Obtain credentials from public supervisor APIs. Never invent observations or IDs.
Do not access hidden answers, evaluation code, databases or controller files.
When querying a set, apply the task's filters and inspect all relevant pages.
Execution errors may leave earlier changes in place; inspect state before retrying.

Verify the requested result and finish with apis.supervisor.complete_task(...).
For action-only tasks without a requested report, omit answer. When an answer is
requested, follow its specified format; for one amount or count with no specified
format, pass a number without units or explanatory text. Never claim success just
because code ran. If unable to finish, use complete_task(status='fail').
"""


def now():
    # AppWorld freezes datetime and time.monotonic; this clock stays real.
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def identity():
    return {
        "revision": REVISION,
        "system_sha256": digest(SYSTEM),
        "tools_sha256": digest(TOOLS),
    }


def public_messages(world):
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "instruction": world.task.instruction,
                    "supervisor": dict(world.task.supervisor),
                    "datetime": str(world.task.datetime),
                },
                ensure_ascii=False,
            ),
        },
    ]


def codes(message, history):
    calls = message.get("tool_calls", [])
    if len(calls) != 1:
        raise ValueError("Reply with exactly one appworld(code) tool call")
    call = calls[0]
    if call["function"]["name"] != "appworld":
        raise ValueError("Only appworld is a model tool; use apis.app.api inside code")
    args = json.loads(call["function"]["arguments"])
    Draft202012Validator(TOOLS[0]["function"]["parameters"]).validate(args)
    if not args["code"].strip():
        raise ValueError("appworld code must not be empty")
    validate_history(
        history + [message, {"role": "tool", "tool_call_id": call["id"], "content": ""}]
    )
    return call, args["code"]
