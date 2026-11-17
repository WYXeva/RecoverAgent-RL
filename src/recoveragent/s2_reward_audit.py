"""Read-only regression against private exported history, not online resampling."""

import argparse
import json
from pathlib import Path
from jsonschema import ValidationError

from .adapter import call_code
from .coevolve_goal import objective, finish_semantic
from .completion_contract import from_instruction
from .demonstrations import sha256
from .harness import public_tool_error
from .io import digest, read_json, write_json
from .v2_model import parse_native


def observed(row):
    actions = []
    for index, item in enumerate(row["business_actions"]):
        args, result = item["arguments"], item["result"]
        args = json.loads(args) if isinstance(args, str) else args
        actions.append(
            {
                "name": item["name"],
                "args": args,
                "result": result,
                "failed": public_tool_error(result),
                "evidence_ref": {
                    "rollout_id": row["history_path"] + "#export_business_actions",
                    "action_index": index,
                },
            }
        )
    return {
        "instruction": row["question"],
        "status": row["student_terminal_status"],
        "answer": row["student_terminal_answer"],
        "before": {},
        "after": {},
        "actions": actions,
    }


def run(source, plan_path, output):
    source, plan_path, output = Path(source), Path(plan_path), Path(output)
    plan, export = read_json(plan_path), read_json(source)
    if sha256(source) != plan["evidence_sha256"]:
        raise ValueError("Private regression evidence changed")
    rows = export["rows"]
    if len(rows) != 48:
        raise ValueError("Expected the frozen 48 original trajectories")
    parsed, goals, annotations = [], [], []
    for row in rows:
        for attempt in row["model_submissions"]:
            item = {
                "row": row["row"],
                "request": attempt["request"],
                "raw_sha256": digest(attempt["raw_model_xml"]),
            }
            try:
                message = parse_native(
                    attempt["raw_model_xml"],
                    "offline",
                    "stop",
                    plan["completion_tools"],
                    {"tool_format": "qwen_xml"},
                    answer_contract=from_instruction(row["question"]),
                )
                accepted = []
                for call in message.get("tool_calls", []):
                    f = call["function"]
                    if f["name"] == "supervisor__complete_task":
                        accepted.append(
                            call_code(
                                f["name"], f["arguments"], plan["completion_tools"]
                            )[1]
                        )
                item.update(schema_valid=bool(accepted), parsed_submissions=accepted)
            except (ValueError, TypeError, KeyError, ValidationError) as exc:
                item.update(schema_valid=False, reason=str(exc))
            parsed.append(item)
        # Derived audit only: do NOT feed this as fresh on-policy data or overwrite
        # old rewards. Proven contaminated history is ineligible as a model negative.
        contaminated = (
            bool(row.get("answer_schema_rejections"))
            or row.get("independent_audit_success") is True
        )
        annotations.append(
            {
                "row": row["row"],
                "original_success": row["original_success"],
                "historical_reward_usable": False if contaminated else None,
                "origin": "parser_or_scorer" if contaminated else "not_reclassified",
            }
        )
    for case in plan["cases"]:
        row = rows[case["row"] - 1]
        evidence = observed(row)
        task = {
            "instruction": row["question"],
            "success_conditions": case["success_conditions"],
        }
        result = objective(task, evidence)
        if result["status"] == "needs_semantic" and case.get(
            "archived_human_text_review"
        ):
            review = case["archived_human_text_review"]
            if review["evidence_sha256"] != digest(evidence):
                raise ValueError(
                    "Prior manual text judgment does not bind to this evidence"
                )
            result = finish_semantic(result, review["judgment"])
            result["semantic_source"] = "archived_human_review; no new model call"
        if result["success"] is not case["expected"]:
            raise AssertionError(
                f"Offline goal regression failed row {case['row']}: {result}"
            )
        goals.append(
            {"row": case["row"], "expected": case["expected"], "result": result}
        )
    summary = {
        "scope": "offline original evidence plus explicitly bound prior human text reviews",
        "episodes": len(rows),
        "completion_responses": len(parsed),
        "schema_valid_responses": sum(p["schema_valid"] for p in parsed),
        "goal_cases": len(goals),
        "goal_success_cases": sum(g["expected"] for g in goals),
        "goal_negative_cases": sum(not g["expected"] for g in goals),
        "source_sha256": sha256(source),
        "plan_sha256": sha256(plan_path),
        "model_requests": 0,
        "new_environment_executions": 0,
        "historical_rewards_changed": 0,
        "fresh_rollouts": 0,
    }
    write_json(output / "parsed.json", parsed)
    write_json(output / "goals.json", goals)
    write_json(output / "historical_reward_annotations.json", annotations)
    write_json(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.evidence, args.plan, args.output), ensure_ascii=False, indent=2
        )
    )
