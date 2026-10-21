"""Authored API-selection examples, never benchmark labels or executed Teacher data."""

STRUCTURED_VARIANT = "dependency_examples_v2"
SELECTION_RULES = """API SELECTION CONTRACT (dependency_examples_v2)
Select a sufficient, relevant API set for an executor starting from the original
task state. The executor has the public task/account identity but has not logged
in, obtained credentials/tokens, looked up identifiers, or performed any action.
It receives only your selected schemas and cannot add tools during execution.

1. Identify the requested result, scope, constraints, and evidence of completion.
2. Work backwards from each necessary operation. Include public APIs that supply
   its required inputs; repeat until the inputs come from the task or obtainable
   observations. Account for credentials, login, access tokens, object IDs,
   related resources, and cross-app dependencies. Never assume preparation by
   another agent or invent an API, identifier, credential, or token.
3. Include listing/search and detail APIs needed for complete coverage, pagination,
   filtering, comparisons, and ties. A summary may omit the required detail.
   Paginated APIs may be called repeatedly; list each API only once.
4. Include public read APIs needed to check a requested change or final result,
   and supervisor.complete_task. Do not select hidden answers, internal database
   access, state management, evaluator/admin tools, or api_docs discovery tools.
5. Select one sufficient dependency path and plausible necessary alternatives,
   not every API in a related app. Do not add writes to a read-only task. A fixed
   list missing a prerequisite blocks execution even if the final operation is
   present. Prefer completing dependencies over unrelated optional APIs.
6. Only use names from the supplied public catalogue. Output at most 20 unique
   APIs, including supervisor.complete_task, one app.api per line, with no prose,
   bullets, code fences, arguments, or execution results. This is a schema-selection
   bound, not a bound on how many times the executor can call an API.

The following TWO examples are human-authored selection illustrations checked
against public API documentation. They are not real tasks, executed trajectories,
official required-API labels, or evidence of success. Their explanatory notes teach
dependencies; assistant example outputs demonstrate only the required API-list
format. Select for the ACTUAL task after the examples; do not copy unrelated APIs.
"""

EXAMPLES = (
    {
        "id": "synthetic_note_pin_v1",
        "task": (
            "In Simple Note, pin my note with the exact title 'Orchid packing list'. "
            "There is exactly one such note. Preserve its title, content, and tags."
        ),
        "dependencies": (
            "Obtain the Simple Note password and log in with the public account "
            "email. Search notes, paging if necessary, and match the exact title "
            "to obtain note_id. Read its current fields, update only pinned when "
            "needed, then read it again to confirm the intended change and "
            "preservation of the other fields. Complete the non-question task "
            "without inventing an answer. Search results do not contain note content."
        ),
        "apis": (
            "supervisor.show_account_passwords",
            "simple_note.login",
            "simple_note.search_notes",
            "simple_note.show_note",
            "simple_note.update_note",
            "supervisor.complete_task",
        ),
    },
    {
        "id": "synthetic_wishlist_reviews_v1",
        "task": (
            "For every product in my Amazon wish list, report its name and the "
            "number of reviews rated exactly one star. Include products with zero "
            "such reviews. Do not change my wish list or buy anything."
        ),
        "dependencies": (
            "Obtain credentials and log in to read the private wish list. Its "
            "entries provide product IDs and names, but not the requested review "
            "counts. For each distinct product ID, read product reviews with the "
            "one-star filter and traverse all pages. The reviews endpoint itself "
            "does not require authentication. Include zero counts and check that "
            "every wish-list product is covered before submitting the answer. "
            "No purchase, review-writing, or wish-list modification API is needed."
        ),
        "apis": (
            "supervisor.show_account_passwords",
            "amazon.login",
            "amazon.show_wish_list",
            "amazon.show_product_reviews",
            "supervisor.complete_task",
        ),
    },
)


def example_messages():
    messages = []
    for example in EXAMPLES:
        messages.extend(
            [
                {
                    "role": "user",
                    "content": (
                        "SYNTHETIC SELECTION EXAMPLE: "
                        + example["id"]
                        + "\nTask: "
                        + example["task"]
                        + "\nDependency explanation: "
                        + example["dependencies"]
                        + "\nShow the sufficient API list in the required output format."
                    ),
                },
                {"role": "assistant", "content": "\n".join(example["apis"])},
            ]
        )
    return messages


def supplement_spec():
    return {"rules": SELECTION_RULES, "examples": example_messages()}
