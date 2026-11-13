#!/usr/bin/env python3
"""CPU-only public API read/write and snapshot probes in a disposable train world."""

import argparse
import json
import sys
from pathlib import Path

from recoveragent.adapter import AppWorldAdapter
from recoveragent.appworld_environment import (
    REVISION,
    execution_environment_error,
    verify_environment,
)
from recoveragent.evaluation import environment_check
from recoveragent.io import write_json
from recoveragent.schema_compatibility import SCHEMA_REVISION
from recoveragent.v2_tasks import context_for


def probe(root, task, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    metadata = verify_environment(REVISION)
    metadata["memory_uri"] = environment_check()
    adapter = AppWorldAdapter(
        root,
        task,
        "environment_probe_" + output.name,
        max_interactions=sys.maxsize,
        research=context_for(task, training=True),
    )
    records, cases, tokens = [], [], {}
    try:
        adapter.configure_schema_compatibility(SCHEMA_REVISION, output)
        tools = adapter.initial_tools()
        public = adapter.public_task()
        initial = adapter.fingerprint()

        def call(name, /, *, expected_business_error=False, **arguments):
            nonlocal tools
            if name not in {t["function"]["name"] for t in tools}:
                app, api = name.split("__", 1)
                call("api_docs__show_api_doc", app_name=app, api_name=api)
            schema = next(t for t in tools if t["function"]["name"] == name)
            if "access_token" in schema["function"]["parameters"]["properties"]:
                app = name.split("__", 1)[0]
                if app in tokens:
                    arguments.setdefault("access_token", tokens[app])
            result, tools = adapter.execute(name, arguments, tools)
            entry = {"name": name, "arguments": arguments, "result": result}
            records.append(entry)
            write_json(output / "calls" / f"{len(records):03}.json", entry)
            failure = result.lstrip().startswith("Execution failed.")
            if expected_business_error:
                if not failure or execution_environment_error(result):
                    raise RuntimeError("Expected normal API rejection: " + name)
                return None
            if failure:
                raise RuntimeError("API probe failed; see private call: " + name)
            return json.loads(result)

        passwords = {
            row["account_name"]: row["password"]
            for row in call("supervisor__show_account_passwords")
        }

        def login(app):
            tokens[app] = call(
                app + "__login",
                username=public["supervisor"]["phone_number"]
                if app == "phone"
                else public["supervisor"]["email"],
                password=passwords[app],
            )["access_token"]

        def simple_note():
            row = call(
                "simple_note__create_note",
                title="Environment probe",
                content="one",
                tags=None,
            )
            key = {"note_id": row["note_id"]}
            call("simple_note__update_note", **key, content="two", pinned=None)
            assert call("simple_note__show_note", **key)["content"] == "two"
            call("simple_note__delete_note", **key)
            call("simple_note__show_note", **key, expected_business_error=True)

        def todoist():
            project = call("todoist__create_project", name="Environment probe")
            row = call(
                "todoist__create_task",
                project_id=project["project_id"],
                title="Probe task",
                due_date="2023-05-20",
                duration=30,
                duration_unit="minutes",
            )
            key = {"task_id": row["task_id"]}
            call("todoist__update_task", **key, is_completed=True, title=None)
            assert call("todoist__show_task", **key)["is_completed"] is True
            call("todoist__delete_task", **key)
            call("todoist__show_task", **key, expected_business_error=True)

        def file_system():
            call(
                "file_system__create_directory",
                directory_path="~/environment_probe",
                recursive=True,
            )
            key = {"file_path": "~/environment_probe/probe.txt"}
            call("file_system__create_file", **key, content="one")
            call("file_system__update_file", **key, content="two")
            assert call("file_system__show_file", **key)["content"] == "two"
            call("file_system__delete_file", **key)
            call("file_system__show_file", **key, expected_business_error=True)

        def gmail():
            row = call(
                "gmail__create_draft",
                recipient_email_addresses=[public["supervisor"]["email"]],
                subject="Environment probe",
                body="one",
            )
            key = {"draft_id": row["draft_id"]}
            call("gmail__update_draft", **key, body="two", scheduled_send_at=None)
            assert call("gmail__show_draft", **key)["body"] == "two"
            call("gmail__delete_draft", **key)
            call("gmail__show_draft", **key, expected_business_error=True)

        def phone():
            row = call("phone__create_alarm", time="08:15", label="Environment probe")
            key = {"alarm_id": row["alarm_id"]}
            call("phone__update_alarm", **key, enabled=False, repeat_days=None)
            assert call("phone__show_alarm", **key)["enabled"] is False
            call("phone__delete_alarm", **key)

        def spotify():
            row = call("spotify__create_playlist", title="Environment probe")
            key = {"playlist_id": row["playlist_id"]}
            call(
                "spotify__update_playlist", **key, title="Probe updated", is_public=None
            )
            assert call("spotify__show_playlist", **key)["title"] == "Probe updated"
            songs = call("spotify__show_song_library", page_limit=1)
            if not songs:
                songs = call("spotify__search_songs", page_limit=1)
            assert songs
            call("spotify__add_song_to_playlist", **key, song_id=songs[0]["song_id"])
            assert len(call("spotify__show_playlist", **key)["songs"]) == 1
            call(
                "spotify__remove_song_from_playlist", **key, song_id=songs[0]["song_id"]
            )
            call("spotify__delete_playlist", **key)

        def splitwise():
            row = call(
                "splitwise__create_group", name="Environment probe", member_emails=[]
            )
            key = {"group_id": row["group_id"]}
            call("splitwise__update_group", **key, description="updated")
            assert call("splitwise__show_group", **key)["description"] == "updated"
            call("splitwise__delete_group", **key)

        def venmo():
            friends = call("venmo__search_friends", page_limit=1)
            assert friends
            row = call(
                "venmo__create_payment_request",
                user_email=friends[0]["email"],
                amount=1,
                description="Environment probe",
                private=True,
            )
            key = {"payment_request_id": row["payment_request_id"]}
            call("venmo__update_payment_request", **key, amount=2)
            rows = call(
                "venmo__show_sent_payment_requests",
                query="Environment probe",
                page_limit=20,
            )
            assert any(
                r["payment_request_id"] == row["payment_request_id"]
                and r["amount"] == 2
                for r in rows
            )
            call("venmo__delete_payment_request", **key)

        def amazon():
            addresses = call("amazon__show_addresses")
            if not addresses:
                address = call("supervisor__show_addresses")[0]
                call("amazon__add_address", **address)
                addresses = call("amazon__show_addresses")
            cards = call("supervisor__show_payment_cards")
            date = public["datetime"]
            valid = [
                c
                for c in cards
                if (c["expiry_year"], c["expiry_month"])
                > (int(date[:4]), int(date[5:7]))
            ]
            assert valid
            # Pick a low-cost, available product using public catalogue results.
            products = call(
                "amazon__search_products", max_price=10, sort_by="+price", page_limit=20
            )
            product = next(p for p in products if p["inventory_quantity"] > 0)
            call(
                "amazon__add_product_to_cart",
                product_id=product["product_id"],
                clear_cart_first=True,
            )
            existing = call("amazon__show_payment_cards")
            for card in valid:
                if not any(
                    c.get("card_number") == card["card_number"] for c in existing
                ):
                    call("amazon__add_payment_card", **card)
            existing = call("amazon__show_payment_cards")
            order = None
            for card in existing:
                try:
                    order = call(
                        "amazon__place_order",
                        address_id=addresses[0]["address_id"],
                        payment_card_id=card["payment_card_id"],
                    )
                    break
                except RuntimeError:
                    if execution_environment_error(records[-1]["result"]):
                        raise
            assert order is not None
            actual = call("amazon__show_order", order_id=order["order_id"])
            assert len(actual["order_items"]) == 1
            assert actual["order_items"][0]["product_id"] == product["product_id"]
            assert not call("amazon__show_cart")["cart_items"]

        for case in (
            simple_note,
            todoist,
            file_system,
            gmail,
            phone,
            spotify,
            splitwise,
            venmo,
            amazon,
        ):
            start = len(records)
            try:
                login(case.__name__)
                case()
                result = {"app": case.__name__, "passed": True}
            except Exception as exc:
                result = {
                    "app": case.__name__,
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            result["calls"] = len(records) - start
            cases.append(result)
            write_json(output / "cases" / (case.__name__ + ".json"), result)
            print(json.dumps(result), flush=True)
        changed = adapter.fingerprint()
        assert changed != initial
        adapter.export_state("environment_probe", output / "snapshot")
        adapter.import_state(output / "snapshot", "environment_probe_reload")
        assert adapter.fingerprint() == changed
        assert adapter.world.task.ground_truth is None
        adapter.save_terminal()
        metadata.update(snapshot_roundtrip=True, hidden_ground_truth_loaded=False)
    finally:
        adapter.close()
    summary = {
        "environment": metadata,
        "task_id": task,
        "cases": cases,
        "calls": len(records),
        "model_requests": 0,
        "passed": len(cases) == 9 and all(c["passed"] for c in cases),
    }
    write_json(output / "summary.json", summary)
    if not summary["passed"]:
        raise RuntimeError("Environment probes failed; inspect private evidence")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    probe(args.root, args.task, args.output)
