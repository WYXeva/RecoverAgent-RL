"""Read-only replay of ALL recorded P3-C request prefixes; not a model A/B score."""

import argparse
from pathlib import Path
import time

from recoveragent.base_sft import load_tokenizer
from recoveragent.context_compiler import ContextBudgetError, compile_context, encode
from recoveragent.io import digest, read_json, write_json
from recoveragent.task_memory import TaskMemory, memory_config


def audit(root, tokenizer):
    rows = []
    for episode in sorted(Path(root).glob("runs/p3c_*/*/interaction")):
        request_paths = sorted((episode / "requests").glob("*.request.json"))
        if not request_paths:
            continue
        task = episode.parent.name
        first = read_json(request_paths[0])
        memory = TaskMemory(task, first["messages"], memory_config({"enabled": True}))
        measurements, sources = [], {}
        started = time.monotonic()
        for path in request_paths:
            request = read_json(path)
            sources[str(path.relative_to(root))] = digest(request)
            original = encode(tokenizer, request["messages"], request["tools"])
            if original != request["input_token_ids"]:
                raise ValueError(
                    "Pinned tokenizer replay differs from actual original request"
                )
            row = {
                "request_id": path.stem.split(".")[0],
                "original_tokens": len(original),
            }
            try:
                _, ids, receipt = compile_context(
                    tokenizer,
                    request["messages"],
                    request["tools"],
                    memory,
                    max_tokens=32768,
                    reserve_tokens=request["max_new_tokens"],
                )
                row.update(
                    compiled_tokens=len(ids),
                    compressed=receipt["compressed"],
                    saved_tokens=len(original) - len(ids),
                )
            except ContextBudgetError as exc:
                row.update(
                    compiled_tokens=None,
                    compressed=False,
                    saved_tokens=None,
                    reason=str(exc),
                )
            measurements.append(row)
            turn = episode / "turns" / (row["request_id"] + ".json")
            if turn.exists():
                value = read_json(turn)
                sources[str(turn.relative_to(root))] = digest(value)
                event = value["event"]
                if event["status"] == "complete_boundary" and value["messages"][
                    event["assistant_index"]
                ].get("tool_calls"):
                    memory.observe(value["messages"], event)
        rows.append(
            {
                "task_id": task,
                "prefixes": measurements,
                "seconds": time.monotonic() - started,
                "source_receipts_sha256": digest(sources),
            }
        )
    if not rows:
        raise ValueError("No original P3-C prefixes found")
    prefixes = [p for r in rows for p in r["prefixes"]]
    return {
        "scope": "offline same-prefix token replay; no new model responses or task outcomes",
        "tasks": len(rows),
        "requests": len(prefixes),
        "compressed_requests": sum(p["compressed"] for p in prefixes),
        "unfit_requests": sum(p["compiled_tokens"] is None for p in prefixes),
        "original_tokens": sum(p["original_tokens"] for p in prefixes),
        "compiled_tokens_fitting_only": sum(
            p["compiled_tokens"] or 0 for p in prefixes
        ),
        "saved_tokens_fitting_only": sum(p["saved_tokens"] or 0 for p in prefixes),
        "real_model_requests": 0,
        "rows": rows,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    result = audit(Path(args.input), load_tokenizer())
    write_json(args.output, result)
    print({k: v for k, v in result.items() if k != "rows"})


if __name__ == "__main__":
    main()
