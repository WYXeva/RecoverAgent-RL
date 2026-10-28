"""Read-only, reproducible export of fresh parallel Teacher evidence.

Run after collection stops. Public output contains metadata only; complete
messages, schemas, model reasoning and official evaluation stay private.
"""

import argparse
from collections import Counter
import fcntl
from pathlib import Path

from recoveragent.demonstrations import as_demo, sha256, validate_demo
from recoveragent.fresh_teacher import campaign_config
from recoveragent.io import canonical, files_manifest, read_json, write_json
from recoveragent.parallel_teacher import DEFAULT, read_review


def export(config_path, campaign, destination, public_json):
    config, plan = campaign_config(config_path)
    if read_json(campaign / "campaign.json") != config:
        raise ValueError("Campaign source configuration differs")
    with (campaign / "campaign.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        destination.mkdir(parents=True, exist_ok=False)
        rows, candidates, clean_successes, sources = [], [], [], []
        response_names = Counter()
        for item in plan:
            row = {
                key: item[key] for key in ("task_id", "scenario_id", "official_split")
            }
            home = campaign / "reviews" / item["task_id"]
            if not (home / "manifest.json").exists():
                row.update(
                    status="NOT_RUN",
                    official_success=None,
                    eligible_for_sft=False,
                    errors=[],
                )
                rows.append(row)
                continue
            review = read_review(home)
            source = Path(review["directory"])
            for receipt in (source / "requests").glob("*.response.json"):
                response_names[str(read_json(receipt).get("model"))] += 1
            if (source / "evidence_manifest.json").exists():
                expected = read_json(source / "evidence_manifest.json")["files"]
                actual = files_manifest(source)
                actual.pop("evidence_manifest.json")
                if actual != expected:
                    raise ValueError("Original attempt evidence changed")
            result = (
                read_json(source / "result.json")
                if (source / "result.json").exists()
                else {}
            )
            if (
                review["official_success"] is not None
                and result.get("official_success") != review["official_success"]
            ):
                raise ValueError("Review disagrees with original official result")
            row.update(
                status=review["original_status"],
                official_success=review["official_success"],
                eligible_for_sft=review["eligible_for_sft"],
                errors=review["errors"],
                termination_reason=review.get("termination_reason"),
                usage=review["usage"],
                tool_calls=review["completed_tool_results"],
                unresolved_tool_intents=review.get("unresolved_tool_intents", 0),
                collection_campaign=source.parent.parent.name,
                source_worker_config_sha256=sha256(source.with_suffix(".config.json"))
                if source.with_suffix(".config.json").exists()
                else None,
                business_api_errors=review.get("business_api_errors"),
                post_stop_diagnostic=review.get("post_stop_diagnostic"),
                source=str(source),
                review_sha256=sha256(home / "review.json"),
                source_manifest_sha256=sha256(source / "evidence_manifest.json")
                if (source / "evidence_manifest.json").exists()
                else None,
                trajectory_sha256=review["trajectory_sha256"],
                assistant_samples=result.get("assistant_samples", 0),
                supervised_tokens=result.get("supervised_tokens", 0),
                longest_sequence=result.get("longest_sequence"),
                replay_verified=result.get("replay_verified", False),
            )
            if (
                result.get("official_success")
                and result.get("replay_verified")
                and result.get("status") in {"SUCCEEDED", "DEFERRED"}
            ):
                record = as_demo(read_json(source / "trajectory.json"))
                validate_demo(record)
                if any(
                    "reasoning_content" in message for message in record["messages"]
                ):
                    raise ValueError("Reasoning leaked into clean Student messages")
                clean_successes.append(record)
                if row["eligible_for_sft"]:
                    candidate = read_json(source / "candidate.json")
                    validate_demo(candidate)
                    if (
                        candidate["messages"] != record["messages"]
                        or candidate["turns"] != record["turns"]
                    ):
                        raise ValueError("Candidate changed actual visible history")
                    candidates.append(candidate)
            elif row["eligible_for_sft"]:
                raise ValueError("Invalid positive candidate")
            sources.append(
                {
                    key: row[key]
                    for key in (
                        "task_id",
                        "scenario_id",
                        "official_split",
                        "source",
                        "source_manifest_sha256",
                        "trajectory_sha256",
                        "review_sha256",
                        "eligible_for_sft",
                    )
                }
            )
            rows.append(row)
        errors = [row for row in rows if row["errors"]]
        for name, records in (
            ("trainable_candidates.jsonl", candidates),
            ("successful_clean.jsonl", clean_successes),
            ("error_register.jsonl", errors),
        ):
            with (destination / name).open("x") as stream:
                stream.writelines(canonical(record) + "\n" for record in records)
        summary = {
            "planned_tasks": len(plan),
            "response_model_labels": dict(response_names),
            "attempted_tasks": sum(r["status"] != "NOT_RUN" for r in rows),
            "official_successes": sum(r["official_success"] is True for r in rows),
            "official_failures": sum(r["official_success"] is False for r in rows),
            "unknown_original_results": sum(
                r["status"] != "NOT_RUN" and r["official_success"] is None for r in rows
            ),
            "trainable_candidates": len(candidates),
            "clean_successes_including_deferred": len(clean_successes),
            "not_run": sum(r["status"] == "NOT_RUN" for r in rows),
            "tasks_with_any_error": len(errors),
            "successes_with_corrected_or_business_errors": sum(
                r["official_success"] is True for r in errors
            ),
            "error_types": dict(
                Counter(error for row in rows for error in row["errors"])
            ),
            "statuses": dict(Counter(row["status"] for row in rows)),
            "assistant_samples": sum(r.get("assistant_samples", 0) for r in rows),
            "supervised_tokens": sum(r.get("supervised_tokens", 0) for r in rows),
            "longest_eligible_sequence": max(
                (r.get("longest_sequence") or 0 for r in rows if r["eligible_for_sft"]),
                default=0,
            ),
            "post_stop_diagnostics": {
                "valid_success": sum(
                    r.get("post_stop_diagnostic", {}).get("success") is True
                    for r in rows
                    if r.get("post_stop_diagnostic")
                ),
                "valid_failure": sum(
                    r.get("post_stop_diagnostic", {}).get("success") is False
                    for r in rows
                    if r.get("post_stop_diagnostic")
                ),
                "unknown": sum(
                    r.get("post_stop_diagnostic", {}).get("success") is None
                    for r in rows
                    if r.get("post_stop_diagnostic")
                ),
                "original_results_unchanged": True,
            },
            "usage_including_inherited_tasks_excluding_setup_diagnostics": {
                key: sum(row.get("usage", {}).get(key, 0) for row in rows)
                for key in (
                    "requests",
                    "inputs",
                    "outputs",
                    "cached",
                    "cost",
                    "unknown_cost",
                )
            },
            "setup_diagnostics": config["fresh_campaign"]["setup_diagnostics"],
            "tool_calls_excluding_setup_diagnostics": sum(
                row.get("tool_calls", 0) for row in rows
            ),
        }
        manifest = {
            "format": "full732-fresh-parallel-candidates-v1",
            "config_sha256": sha256(config_path),
            "runtime_lock_sha256": sha256(config["protocol_lock"]),
            "export_script_sha256": sha256(__file__),
            "split_manifest_sha256": config["fresh_campaign"]["split_manifest_sha256"],
            "student": config["student"],
            "teacher": config["teacher"],
            "summary": summary,
            "sources": sources,
            "files": files_manifest(destination),
            "training_started": False,
            "complete_a244": summary["attempted_tasks"] == len(plan),
            "visibility": "Student messages exclude provider reasoning and controller evaluation. successful_clean includes deferred; only trainable_candidates passed current32768/4096 encoding and parser.",
        }
        write_json(destination / "manifest.json", manifest)
        (destination / "manifest.sha256").write_text(
            sha256(destination / "manifest.json") + "  manifest.json\n"
        )
        public = {
            "summary": summary,
            "rows": rows,
            "dataset": str(destination),
            "dataset_manifest_sha256": sha256(destination / "manifest.json"),
            "dataset_file_sha256": manifest["files"],
            "global_stop": read_json(campaign / "global_stop.json")
            if (campaign / "global_stop.json").exists()
            else None,
            "error_definition": "Includes successfully corrected protocol/API errors. Do not count every error-bearing task as failed. Original unknown and post-stop diagnosis remain separate.",
        }
        write_json(public_json, public)
        return public


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--public-json", type=Path, required=True)
    args = parser.parse_args()
    print(
        export(args.config, args.campaign, args.destination, args.public_json)[
            "summary"
        ]
    )
