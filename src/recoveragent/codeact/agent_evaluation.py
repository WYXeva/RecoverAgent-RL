"""Offline metrics and a bounded, shared, public-evidence execution-quality Judge."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import threading

from recoveragent.coevolve_generation import json_object
from recoveragent.io import digest, read_json, write_json
from recoveragent.teacher import (
    TeacherPolicy,
    normalize_message,
    read_credentials,
    response_model_matches,
)
from recoveragent.teacher_context import receipt

from .collect import replace_json
from .evolution import Budget
from .execution_audit import (
    DIMENSIONS,
    JUDGE,
    REVISION,
    analyze,
    judge_input,
    validate_judgment,
)


def judge_one(item, config, credentials, output, stop):
    directory = Path(item["directory"])
    data = judge_input(directory)
    target = Path(output) / item["split"] / item["model"] / directory.name
    binding = {
        "revision": REVISION,
        "input_sha256": digest(data),
        "prompt_sha256": digest(JUDGE),
        "judge": config["teacher"]["judge_text"],
    }
    if (target / "binding.json").exists():
        if read_json(target / "binding.json") != binding:
            raise ValueError("Execution rubric identity changed")
    else:
        write_json(target / "binding.json", binding)
    if (target / "result.json").exists():
        return read_json(target / "result.json")
    if stop.is_set():
        return {"status": "service_blocked_not_called"}
    try:
        policy = TeacherPolicy(
            {
                "teacher": config["teacher"],
                "authorization": config["authorization"],
                "tool_mode": "appworld_code",
            },
            credentials,
        )
        request = policy.build_text_request(
            [
                {"role": "system", "content": JUDGE},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ],
            8192,
            role="judge",
        )
        if (target / "response.json").exists():
            response = read_json(target / "response.json")
            if read_json(target / "request.json") != request:
                raise ValueError("Rubric request changed on resume")
        elif (target / "request.json").exists():
            raise ValueError(
                "Interrupted Judge request; usage unknown, no automatic replay"
            )
        else:
            audit = receipt(request, config["teacher_context"])
            write_json(target / "context.json", audit)
            if not audit["within_budget"]:
                raise ValueError("Rubric input exceeds Teacher window; no truncation")
            write_json(target / "request.json", request)
            response = Budget(Path(output) / "ledger", config, None).call(
                request, policy.transport, "execution_rubric"
            )
            write_json(target / "response.json", response)
        if not response_model_matches(config["teacher"], response.get("model")):
            raise ValueError("Rubric model response differs")
        message = normalize_message(response, allow_reasoning=True)
        value = validate_judgment(
            json_object(message["content"]), len(data["execution"])
        )
        result = {
            "status": "valid",
            "scores": value,
            "total": sum(v["score"] for v in value.values()),
            "usage": response.get("usage"),
            "reasoning_present": bool(
                response["choices"][0]["message"].get("reasoning_content")
            ),
        }
    except Exception as exc:
        if any(v in str(exc) for v in ("HTTP 401", "HTTP 403", "HTTP 429")):
            stop.set()
        result = {
            "status": "unavailable",
            "error_type": type(exc).__name__,
            "reason": str(exc),
        }
    write_json(target / "result.json", {**binding, **result})
    return result


def judge_all(items, config, credentials, output):
    if config["teacher"]["judge_text"]["prompt_sha256"] != digest(JUDGE):
        raise ValueError("Frozen rubric prompt differs")
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda item: judge_one(item, config, credentials, output, stop), items
            )
        )
    print(json.dumps(dict(Counter(r["status"] for r in results))))


def csv_file(path, rows, fields=None):
    with Path(path).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def report(items, judges, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    rubrics = []
    failures = defaultdict(list)
    for item in items:
        row = analyze(item["directory"], item["model"], item["split"])
        p = (
            Path(judges)
            / item["split"]
            / item["model"]
            / Path(item["directory"]).name
            / "result.json"
        )
        judgment = read_json(p) if p.exists() else {"status": "not_run"}
        row["rubric_total"] = (
            judgment.get("total") if judgment.get("status") == "valid" else None
        )
        row["rubric_status"] = judgment["status"]
        row["rubric_scores"] = {
            k: v["score"] for k, v in judgment.get("scores", {}).items()
        }
        rubrics.append(
            {
                "model": item["model"],
                "split": item["split"],
                "task_id": row["task_id"],
                "trajectory_sha256": row["trajectory_sha256"],
                **judgment,
            }
        )
        for event in row["events"]:
            for kind in event["types"]:
                failures[(item["split"], event["channel"] + ":" + kind)].append(
                    (item["model"], row["task_id"], event["recovered"])
                )
        for key in ["repeat_documentation", "repeat_code", "consecutive_same_error"]:
            failures[(item["split"], key)].extend(
                [(item["model"], row["task_id"], None)] * (row[key] or 0)
            )
        if row["budget_exhausted"]:
            failures[(item["split"], "termination:" + row["termination"])].append(
                (item["model"], row["task_id"], False)
            )
        rows.append(row)
    summaries = []
    for split, model in dict.fromkeys((r["split"], r["model"]) for r in rows):
        group = [r for r in rows if r["split"] == split and r["model"] == model]
        n = len(group)
        success = sum(r["success"] is True for r in group)
        failure = sum(r["success"] is False for r in group)

        def total(k):
            return sum(r[k] or 0 for r in group)

        def ratio(a, b):
            return a / b if b else None

        scores = [r["rubric_total"] for r in group if r["rubric_total"] is not None]
        complete_api = all(r["business_api_attempts"] is not None for r in group)
        summary = {
            "split": split,
            "Model ID": model,
            "Episodes": n,
            "Official Success": success,
            "Official Failure": failure,
            "Unknown": n - success - failure,
            "Pass Rate": ratio(success, success + failure),
            "Mean Rubric Score": sum(scores) / len(scores) if scores else None,
            "Rubric Episodes": len(scores),
            "Format Rejection Rate": ratio(
                total("format_rejections"), total("replies")
            ),
            "Invalid Tool Call Rate": ratio(
                total("invalid_tool_calls"), total("replies")
            ),
            "Code Execution Error Rate": ratio(
                total("code_errors"), total("code_blocks")
            ),
            "Business API Error Rate": ratio(
                total("business_api_errors"), total("business_api_attempts")
            )
            if complete_api
            else None,
            "Repeated Call Rate": ratio(total("repeat_code"), total("code_blocks")),
            "Budget Exhaustion Rate": ratio(total("budget_exhausted"), n),
            "Mean Model Requests": total("requests") / n,
            "Mean Business API Calls": total("business_calls") / n,
            "Mean Input Tokens": total("input_tokens") / n,
            "Mean Output Tokens": total("output_tokens") / n,
            "Mean Execution Time": total("seconds") / n,
        }
        for dim in DIMENSIONS:
            vals = [r["rubric_scores"][dim] for r in group if dim in r["rubric_scores"]]
            summary["Mean " + dim] = sum(vals) / len(vals) if vals else None
        for name in [
            "replies",
            "format_rejections",
            "invalid_tool_calls",
            "code_blocks",
            "code_errors",
            "business_api_attempts",
            "business_api_errors",
            "visible_api_errors",
            "documentation_calls",
            "repeat_documentation",
            "repeat_code",
            "budget_exhausted",
        ]:
            summary[name] = (
                total(name) if all(r[name] is not None for r in group) else None
            )
        summary["format_affected_episodes"] = sum(
            r["format_rejections"] > 0 for r in group
        )
        summary["code_error_affected_episodes"] = sum(
            r["code_errors"] > 0 for r in group
        )
        summary["repeat_code_affected_episodes"] = sum(
            r["repeat_code"] > 0 for r in group
        )
        summary["longest_same_error"] = max(r["longest_same_error"] for r in group)
        summary["longest_same_code"] = max(r["longest_same_code"] for r in group)
        summaries.append(summary)
    csv_file(output / "model_comparison.csv", summaries)
    breakdown = [
        {
            "split": split,
            "error_type": kind,
            "total_occurrences": len(events),
            "affected_episodes": len({(m, t) for m, t, _ in events}),
            "affected_models": ";".join(sorted({m for m, _, _ in events})),
            "recovery_count": sum(v is True for _, _, v in events),
            "unrecovered_count": sum(v is False for _, _, v in events),
            "recovery_unavailable": sum(v is None for _, _, v in events),
        }
        for (split, kind), events in sorted(failures.items())
    ]
    csv_file(output / "harness_failure_breakdown.csv", breakdown)
    fields = [k for k in rows[0] if k not in ("events", "rubric_scores")]
    csv_file(
        output / "episode_metrics.csv",
        [{k: r[k] for k in fields} for r in rows],
        fields,
    )
    paired = []
    models = list(dict.fromkeys(r["model"] for r in rows))
    for split, task in dict.fromkeys((r["split"], r["task_id"]) for r in rows):
        row = {"split": split, "task_id": task}
        found = {
            r["model"]: r for r in rows if r["split"] == split and r["task_id"] == task
        }
        for model in models:
            for key in (
                "success",
                "rubric_total",
                "termination",
                "format_rejections",
                "code_errors",
                "business_api_errors",
                "requests",
                "input_tokens",
                "output_tokens",
                "seconds",
            ):
                row[model + "_" + key] = found.get(model, {}).get(key)
        if all(model in found for model in models):
            outcomes = [found[model]["success"] for model in models]
            row["always_success"] = all(v is True for v in outcomes)
            row["always_failure"] = all(v is False for v in outcomes)
            row["success_then_failure"] = any(
                v is False and True in outcomes[:i] for i, v in enumerate(outcomes)
            )
        else:
            row.update(
                always_success=None, always_failure=None, success_then_failure=None
            )
        paired.append(row)
    csv_file(output / "task_level_comparison.csv", paired)
    (output / "rubric_results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rubrics)
    )
    replace_json(
        output / "metrics.json",
        {"revision": REVISION, "rows": rows, "models": summaries},
    )
    return summaries


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["judge", "report"])
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--judges", required=True)
    parser.add_argument("--output")
    parser.add_argument("--config")
    parser.add_argument("--credentials")
    args = parser.parse_args()
    items = read_json(args.inputs)
    if args.command == "judge":
        judge_all(
            items,
            read_json(args.config),
            read_credentials(args.credentials),
            args.judges,
        )
    else:
        report(items, args.judges, args.output)


if __name__ == "__main__":
    main()
