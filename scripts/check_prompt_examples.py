"""Check authored selection examples against pinned public docs, without executing tasks."""

import argparse
from pathlib import Path

from recoveragent import APPWORLD_REVISION
from recoveragent.demonstrations import sha256
from recoveragent.io import digest, read_json, write_json
from recoveragent.predictor_prompts import EXAMPLES, STRUCTURED_VARIANT, supplement_spec


def check(root):
    root = Path(root) / "data/api_docs/standard"
    apps = {name.split(".")[0] for ex in EXAMPLES for name in ex["apis"]}
    docs = {app: read_json(root / f"{app}.json") for app in apps}
    for example in EXAMPLES:
        for name in example["apis"]:
            app, api = name.split(".")
            assert api in docs[app], name

    def parameters(app, api):
        return {p["name"]: p for p in docs[app][api]["parameters"]}

    def fields(app, api):
        response = docs[app][api]["response_schemas"]["success"]
        return set(response[0] if isinstance(response, list) else response)

    for app in ("simple_note", "amazon"):
        assert {"username", "password"} <= parameters(app, "login").keys()
        assert "access_token" in fields(app, "login")
    assert {"note_id", "title", "pinned"} <= fields("simple_note", "search_notes")
    assert "content" not in fields("simple_note", "search_notes")
    assert {"note_id", "title", "content", "tags", "pinned"} <= fields(
        "simple_note", "show_note"
    )
    assert {"page_index", "page_limit"} <= parameters(
        "simple_note", "search_notes"
    ).keys()
    assert parameters("simple_note", "update_note")["pinned"]["required"] is False
    assert parameters("simple_note", "update_note")["access_token"]["required"] is True
    assert parameters("amazon", "show_wish_list")["access_token"]["required"] is True
    assert {"product_id", "product_name"} <= fields("amazon", "show_wish_list")
    reviews = parameters("amazon", "show_product_reviews")
    assert {
        "product_id",
        "page_index",
        "page_limit",
        "min_rating",
        "max_rating",
    } <= reviews.keys()
    assert "access_token" not in reviews
    assert {"review_id", "product_id", "rating"} <= fields(
        "amazon", "show_product_reviews"
    )
    assert "status" in parameters("supervisor", "complete_task")
    return {
        "status": "PASS",
        "variant": STRUCTURED_VARIANT,
        "appworld_revision": APPWORLD_REVISION,
        "example_ids": [ex["id"] for ex in EXAMPLES],
        "provenance": "human_authored_public_api_selection_only",
        "task_ids_used_as_examples": [],
        "hidden_labels_loaded": False,
        "teacher_demonstrations_generated": 0,
        "environment_calls": 0,
        "model_requests": 0,
        "dependency_and_pagination_contracts_checked": True,
        "supplement_sha256": digest(supplement_spec()),
        "public_api_doc_sha256": {
            app: sha256(root / f"{app}.json") for app in sorted(apps)
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appworld-root", default="data/appworld")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    write_json(args.output, check(args.appworld_root))
    print("PASS: two authored selection examples checked against public API docs")


if __name__ == "__main__":
    main()
