"""All-attempt public-contract audit for v5, without exposing task/answer text."""

import argparse
from collections import Counter
from pathlib import Path

from recoveragent.answer_guard import validate_guard_record
from recoveragent.execution_v5 import actor_system
from recoveragent.io import read_json, write_json
from scripts.audit_v4_collection import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run = Path(args.run)
    report = audit(run, system_factory=actor_system)
    for row in report["rows"]:
        trajectory = read_json(run / row["trajectory_id"] / "trajectory.json")
        if trajectory["status"] == "valid":
            # Interrupted/rejected provider responses are validated by the base audit.
            valid = dict(
                trajectory,
                turns=[t for t in trajectory["turns"] if "format_error" not in t],
            )
            validate_guard_record(valid)
        row["answer_guard"] = trajectory.get("answer_guard")
    report["answer_guard_metrics"] = {}
    for mode in ("api_predictor", "dynamic_discovery"):
        rows = [r for r in report["rows"] if r["tool_mode"] == mode]
        guards = [r["answer_guard"] for r in rows if r["answer_guard"]]
        report["answer_guard_metrics"][mode] = {
            "public_contracts": dict(Counter(g["contract"]["kind"] for g in guards)),
            "first_numeric_submissions": sum(
                g["first_submission_valid"] is not None for g in guards
            ),
            "first_numeric_format_valid": sum(
                g["first_submission_valid"] is True for g in guards
            ),
            "correction_used": sum(g["correction_used"] for g in guards),
            "correction_format_valid": sum(
                g["correction_valid"] is True for g in guards
            ),
            "official_success_after_correction": sum(
                bool(
                    r["answer_guard"]
                    and r["answer_guard"]["correction_used"]
                    and r["official_success"]
                )
                for r in rows
            ),
            "tool_requests": sum(r["usage"].get("tool_requests", 0) for r in rows),
            "appworld_tool_calls": sum(r["usage"]["tool_calls"] for r in rows),
            "harness_rejections": sum(
                r["usage"].get("harness_rejections", 0) for r in rows
            ),
        }
    write_json(args.output, report)
    print({k: report[k] for k in ("coverage", "answer_guard_metrics", "costs")})


if __name__ == "__main__":
    main()
