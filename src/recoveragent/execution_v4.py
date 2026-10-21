"""Explicit completion/payload/counting rules; frozen execution_v3 is unchanged."""

from .p3a_prompts import ACTOR_PROMPTS as LEGACY_PROMPTS
from .p3a_prompts import actor_system as legacy_system

ACTOR_PROMPTS = (*LEGACY_PROMPTS, "execution_v4")

RULES = """DELIVERABLE CONTRACT (check before completing)
- STATE CHANGE ONLY (send, create, update, play, etc., with no requested answer):
  call supervisor.complete_task with {"status":"success"}, or {} if allowed.
  OMIT the answer key entirely. Do not put a progress report, confirmation,
  operation summary, empty string, or null into answer. A brief confirmation may
  appear in ordinary Assistant text, outside the tool's answer parameter.
- ANSWER REQUESTED (including imperative forms such as "Name ..." or "Count ..."):
  provide only the requested concise value in answer, after checking evidence.
  Do not decide task type solely from the presence of a question mark.
- MIXED: perform the requested change and provide an answer only if the user
  explicitly also requested one. Never infer a requirement to summarize actions.
Use the currently visible schema and check this contract immediately before
completion. This is a public API rule, not a query to hidden validation.
Generic protocol examples, NOT observations or solutions to the current task:
  "Start playing my specified playlist": after actual playback and public checks,
  complete_task({"status":"success"}).
  "Give the number of items": after counting actual observations, if the verified
  count is N, complete_task({"answer":N,"status":"success"}); N is not a literal.

EXACT PAYLOADS
When the user specifies an exact output format, content written into the target
app must contain only the requested payload. For a comma-separated list, serialize
verified items as item1, item2, ... without an introduction, explanation, greeting,
or completion summary unless requested. Keep narration outside the tool argument
carrying the payload. Verify stored content, not only the API acknowledgement.

COUNTS AND RANKINGS
Define the counted record and grouping key from the public schema. Maintain a
page-by-page tally using stable IDs. Expand every relevant entry of nested lists;
do not silently keep only the first. Deduplicate repeated records across pages
without discarding legitimate group members. Reconcile the tally total against
observed record/group pairs, then compare the largest counts and any tie rule.
Recheck leading candidates against original observations before submitting.

AUTHENTICATION DEPENDENCIES
If a required input can only be obtained through a tool unavailable in the fixed
tool list, do not substitute another app's token, passwords, user IDs, or guessed
or constructed credentials. Report the missing public dependency and stop with
failure according to the visible completion schema. In discovery mode, discover
the missing prerequisite first. Repeating authentication requires new evidence
or a newly obtained legitimate credential; changing an invented token is not
progress. No controller has logged in or obtained credentials on your behalf.
"""


def actor_system(config):
    if config.get("actor_prompt_variant") != "execution_v4":
        return legacy_system(config)
    base = legacy_system({**config, "actor_prompt_variant": "execution_v3"})
    base = base.replace(
        "a concise answer for a\n   question, no invented answer for an action-only task.",
        "follow the DELIVERABLE CONTRACT below.",
    )
    return base + "\n" + RULES
