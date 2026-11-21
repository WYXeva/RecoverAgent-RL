"""Controller-only execution diagnostics; never changes official task rewards."""

from collections import Counter
import json
from pathlib import Path
import re

from recoveragent.io import digest, read_json

REVISION = "codeact_execution_rubric_v1"


class ApiAudit:
    """Observe the pinned Requester without changing arguments/returns/errors.

    Counts attempts entering _request, including parameter validation failures
    before the upstream request tracker records a dispatch. Untracked controller
    reads and admin operations are excluded. No response bodies enter the rubric.
    """

    def __init__(self, requester, directory):
        self.request_index = None
        self.path = Path(directory) / "api_outcomes.jsonl"
        original = requester._request

        def request(
            _app_name, _api_name, client=None, raise_on_failure=None, track=True, **data
        ):
            app, api = _app_name, _api_name
            record = {
                "request_index": self.request_index,
                "app": app,
                "api": api,
                "status_code": None,
                "error": None,
                "exception_type": None,
            }
            try:
                response = original(
                    app,
                    api,
                    client=client,
                    raise_on_failure=raise_on_failure,
                    track=track,
                    **data,
                )
                record["status_code"] = getattr(response, "status_code", None)
                return response
            except Exception as exc:
                record.update(error=str(exc), exception_type=type(exc).__name__)
                match = re.search(r"Response status code is (\d{3})\b", str(exc))
                if match:
                    record["status_code"] = int(match.group(1))
                raise
            finally:
                if track and app != "admin":
                    with self.path.open("a") as stream:
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")

        requester._request = request


def diagnostics(directory, result):
    directory = Path(directory)
    trajectory_path = directory / "trajectory.json"
    trajectory = read_json(trajectory_path) if trajectory_path.exists() else {}
    actions = trajectory.get("actions")
    if actions is None:
        actions = [
            read_json(p) for p in sorted((directory / "actions").glob("*.result.json"))
        ]
    api_path = directory / "api_outcomes.jsonl"
    apis = (
        [json.loads(line) for line in api_path.read_text().splitlines()]
        if api_path.exists()
        else []
    )
    api_errors = [
        r for r in apis if r["error"] is not None or (r["status_code"] or 0) >= 400
    ]
    api_kinds = Counter()
    for row in api_errors:
        if row["status_code"] is not None:
            key = "http_" + str(row["status_code"])
        elif "No app named" in (row["error"] or "") or "No API named" in (
            row["error"] or ""
        ):
            key = "unknown_app_or_api"
        else:
            key = row["exception_type"] or "unknown"
        api_kinds[key] += 1
    execution_errors = [a for a in actions if a.get("execution_error")]
    python_errors = Counter()
    for action in execution_errors:
        matches = re.findall(r"\b([A-Za-z]+Error):", action["observation"])
        if "Syntax error in line:" in action["observation"]:
            matches.append("SyntaxError")
        python_errors[matches[-1] if matches else "api_or_other_execution_error"] += 1
    tracked = (
        read_json(directory / "api_calls.json")
        if (directory / "api_calls.json").exists()
        else []
    )
    docs = [r for r in tracked if "/api_docs/" in r["url"]]
    doc_keys = [digest({"url": r["url"], "data": r.get("data")}) for r in docs]
    official = result.get("evaluation", {}).get("official", {})
    score_valid = bool(result.get("valid"))
    return {
        "revision": REVISION,
        "format_rejections": trajectory.get(
            "format_rejections", len(list((directory / "rejections").glob("*.json")))
        ),
        "code_blocks": len(actions),
        "code_execution_errors": len(execution_errors),
        "python_error_types": dict(python_errors),
        "api_attempts": len(apis),
        "api_errors": len(api_errors),
        "api_error_types": dict(api_kinds),
        "api_outcomes_recorded": api_path.exists(),
        "tracked_api_calls": len(tracked),
        "documentation_calls": len(docs),
        "repeated_documentation_calls": len(docs) - len(set(doc_keys)),
        "business_calls": sum(
            "/api_docs/" not in r["url"] and "/supervisor/" not in r["url"]
            for r in tracked
        ),
        "loop_warnings": sum(
            a.get("loop_control", {}).get("warn", False) for a in actions
        ),
        "loop_stopped": result.get("termination") == "repeated_error_limit",
        "submitted_but_failed": result.get("termination") == "task_completed"
        and result.get("success") is False,
        "official_checks_passed": len(official.get("passes", []))
        if score_valid
        else None,
        "official_checks_failed": len(official.get("failures", []))
        if score_valid
        else None,
        # Private controller-side rubric; never append requirements to Actor history.
        "official_failed_requirements": [
            r.get("requirement") for r in official.get("failures", [])
        ]
        if score_valid
        else [],
    }


def totals(rows):
    counts = Counter()
    terms = Counter()
    api_types, python_types = Counter(), Counter()
    usage = Counter()
    for row in rows:
        terms[row.get("termination", "unknown")] += 1
        rubric = row.get("rubric", {})
        for name in (
            "format_rejections",
            "code_blocks",
            "code_execution_errors",
            "api_attempts",
            "api_errors",
            "documentation_calls",
            "repeated_documentation_calls",
            "business_calls",
            "loop_warnings",
            "loop_stopped",
            "submitted_but_failed",
            "official_checks_passed",
            "official_checks_failed",
        ):
            if rubric.get(name) is not None:
                counts[name] += rubric[name]
        api_types.update(rubric.get("api_error_types", {}))
        python_types.update(rubric.get("python_error_types", {}))
        usage.update(
            {
                k: row.get("usage", {}).get(k, 0)
                for k in ("requests", "prompt_tokens", "generated_tokens", "seconds")
            }
        )
    return {
        "tasks": len(rows),
        "success": sum(r["success"] is True for r in rows),
        "failure": sum(r["success"] is False for r in rows),
        "unknown": sum(r["success"] is None for r in rows),
        "termination_counts": dict(terms),
        "rubric_totals": dict(counts),
        "api_error_types": dict(api_types),
        "python_error_types": dict(python_types),
        "usage": dict(usage),
        "task_seconds": sum(r.get("seconds", 0) for r in rows),
    }
