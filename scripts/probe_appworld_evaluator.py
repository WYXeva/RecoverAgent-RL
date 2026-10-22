"""Controller-only replay diagnostic; never generates Teacher training examples."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import traceback


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def url_probe():
    from appworld.apps.lib.models.db import Database, get_db_engine

    expected = ":memory:diagnostic"
    engine = get_db_engine(expected + "/spotify.db")
    try:
        url, _, home, _, _ = Database._get_engine_to_details(engine)
        return {
            "sqlalchemy": importlib.metadata.version("sqlalchemy"),
            "sqlmodel": importlib.metadata.version("sqlmodel"),
            "rendered_url": url,
            "database_component": engine.url.database,
            "expected_home": expected,
            "actual_home": home,
            "matches": home == expected,
        }
    finally:
        engine.dispose()


def replay(source, root, namespace):
    from appworld.evaluator import evaluate_task
    from recoveragent.adapter import AppWorldAdapter

    t = json.loads(source.read_text())
    if t["split"] != "train" or t["status"] != "valid":
        raise ValueError("Only completed official train attempts may be diagnosed")
    config = json.loads((source.parent.parent / "config.json").read_text())
    adapter = AppWorldAdapter(
        root,
        t["task_id"],
        namespace,
        seed=config["environment_seed"],
        max_interactions=t["usage"]["tool_calls"] + 1,
    )
    observations, calls = [], {}
    try:
        tools = adapter.initial_tools()
        for message in t["messages"]:
            if message["role"] == "assistant":
                for call in message.get("tool_calls", []):
                    calls[call["id"]] = call["function"]
            elif message["role"] == "tool":
                function = calls[message["tool_call_id"]]
                result, tools = adapter.execute(
                    function["name"], function["arguments"], tools
                )
                matches = result == message["content"]
                observations.append({"tool": function["name"], "exact_match": matches})
                if not matches:
                    raise ValueError("Replay observation diverged; diagnostic stops")
        adapter.save_terminal()
    finally:
        adapter.close()
    result = {
        "scope": "scripted historical action replay; not new Teacher data",
        "source": str(source),
        "source_sha256": sha(source),
        "namespace": namespace,
        "task_id": t["task_id"],
        "real_tool_calls": len(observations),
        "observations": observations,
        "model_requests": 0,
        "teacher_demonstrations_generated": 0,
        "original_reward": t["reward"],
        "original_evidence_modified": False,
        "official_validator_modified": False,
    }
    try:
        tracker = evaluate_task(
            t["task_id"],
            experiment_name=namespace,
            suppress_errors=False,
            save_report=False,
        )
        result["evaluation_success"] = tracker.success
    except Exception as exc:
        # Never publish SQL parameters, hidden expected values or full exception bodies.
        result["evaluation_exception"] = {
            "type": type(exc).__name__,
            "missing_songs_table": "no such table: songs" in str(exc),
            "stack": [
                {
                    "file": Path(frame.filename).name,
                    "line": frame.lineno,
                    "function": frame.name,
                }
                for frame in traceback.extract_tb(exc.__traceback__)
            ],
        }
        bindings, tb = [], exc.__traceback__
        while tb:
            record = tb.tb_frame.f_locals.get("record")
            if record is None:
                record = tb.tb_frame.f_locals.get("self")
            if record is not None and hasattr(record, "_db_home_path"):
                bindings.append(
                    {
                        "function": tb.tb_frame.f_code.co_name,
                        "instance_db_home": record._db_home_path,
                        "class_db_home": record.db.home_path,
                    }
                )
            tb = tb.tb_next
        result["db_bindings_at_exception"] = bindings
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--namespace")
    parser.add_argument("--root", default="data/appworld")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if bool(args.source) != bool(args.namespace):
        raise ValueError("Replay requires both --source and a new --namespace")
    result = {"url_probe": url_probe(), "training_started": False}
    if args.source:
        result["replay"] = replay(args.source, args.root, args.namespace)
    with args.output.open("x") as out:
        json.dump(result, out, indent=2)
        out.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "url_roundtrip_matches": result["url_probe"]["matches"],
                "model_requests": 0,
                "real_tool_calls": result.get("replay", {}).get("real_tool_calls", 0),
                "evaluation_exception": result.get("replay", {})
                .get("evaluation_exception", {})
                .get("type"),
            }
        )
    )


if __name__ == "__main__":
    main()
