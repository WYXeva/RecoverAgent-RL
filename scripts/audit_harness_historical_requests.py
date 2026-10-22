"""Read-only public request audit; detection is not a counterfactual success rate."""

import argparse
from collections import Counter
import json
from pathlib import Path

from recoveragent.demonstrations import sha256
from recoveragent.harness import ProtocolViolation, check_batch
from recoveragent.io import read_json, write_json
from recoveragent.teacher import normalize_message


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    totals, rows = Counter(), []
    for path in sorted(
        Path("runs").glob("base_sft_v1_20261021_b*/teacher_*/trajectory.json")
    ):
        t = read_json(path)
        violations, responses = Counter(), 0
        for r in sorted((path.parent / "requests").glob("*.response.json")):
            response = read_json(r)
            request = read_json(r.with_name(r.name.replace(".response.", ".request.")))
            responses += 1
            try:
                message = normalize_message(response, None)
                check_batch(message, request["messages"], request["tools"])
            except (ValueError, KeyError, TypeError) as exc:
                code = (
                    exc.code
                    if isinstance(exc, ProtocolViolation)
                    else (
                        "truncated_output"
                        if any(
                            c.get("finish_reason") == "length"
                            for c in response.get("choices", [])
                        )
                        else "malformed_output"
                    )
                )
                violations[code] += 1
        totals.update(violations)
        rows.append(
            {
                "trajectory_id": t["trajectory_id"],
                "task_id": t["task_id"],
                "source_sha256": sha256(path),
                "historical_official_success": t.get("reward") == 1,
                "complete_response_receipts": responses,
                "rejected_response_counts": dict(violations),
            }
        )
    result = {
        "scope": "offline public request detection; not a new rollout or repaired result",
        "attempts": len(rows),
        "complete_response_receipts": sum(
            r["complete_response_receipts"] for r in rows
        ),
        "detected_responses": dict(totals),
        "attempts_with_detection": sum(
            bool(r["rejected_response_counts"]) for r in rows
        ),
        "historical_successes_rejected": sum(
            r["historical_official_success"] and bool(r["rejected_response_counts"])
            for r in rows
        ),
        "model_requests": 0,
        "real_tool_calls": 0,
        "training_steps": 0,
        "rows": rows,
    }
    write_json(a.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))


if __name__ == "__main__":
    main()
