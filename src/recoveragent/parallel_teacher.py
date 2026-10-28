"""Bounded process-isolated continuation of the frozen fresh A244 campaign.

Threads only supervise subprocesses; AppWorld worlds never share a process.
Each task is claimed once. Errors are immutable review records, not retry jobs.
"""

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import fcntl
import os
from pathlib import Path
import shutil

from . import fresh_teacher as fresh
from .demonstrations import sha256
from .harness import public_tool_error
from .io import digest, files_manifest, read_json, write_json
from .research_access import ROOT, authorize_task
from .teacher import (
    read_credentials,
    response_model_matches,
    response_usage,
    usage_cost,
)

DEFAULT = ROOT / "configs/full732_a244_teacher_parallel_v2_20261028.json"


def receipt_usage(directory, teacher):
    """Report actual returned usage even for an over-limit, unexecuted reply.

    This audit does NOT change the strict runtime result or its reservation.
    Unknown billing is still reserved; a paid request is never retried here.
    """
    total = dict(requests=0, inputs=0, outputs=0, cached=0, cost=0.0, unknown_cost=0.0)
    errors = []
    for path in sorted((directory / "requests").glob("*.reservation.json")):
        total["requests"] += 1
        reserve = read_json(path)
        response = path.with_name(path.name.replace(".reservation.", ".response."))
        try:
            raw = read_json(response)
            usage = response_usage(raw)
            if not response_model_matches(teacher, raw.get("model")):
                errors.append("provider_model_identity")
                raise ValueError("Unexpected model")
            if not isinstance(raw.get("id"), str) or not raw["id"]:
                raise ValueError("Missing response identity")
        except (OSError, ValueError, KeyError, TypeError):
            total["unknown_cost"] += reserve["max_cost"]
            errors.append("provider_usage_unknown")
            continue
        total["inputs"] += usage["prompt_tokens"]
        total["outputs"] += usage["generated_tokens"]
        total["cached"] += usage["cached_prompt_tokens"]
        total["cost"] += usage_cost(usage, teacher["pricing"])
        if (
            usage["generated_tokens"] > reserve["max_tokens"]
            or usage["prompt_tokens"] > teacher["billing_context_tokens"]
        ):
            errors.append("provider_token_bound")
    return total, sorted(set(errors))


def audit_attempt(config, row, directory, review_home, result, execution_error=None):
    """Keep classification/diagnostic scoring separate from immutable raw results."""
    review_home.mkdir(parents=True, exist_ok=False)
    trajectory_path = directory / "trajectory.json"
    trajectory = read_json(trajectory_path) if trajectory_path.exists() else {}
    usage, errors = receipt_usage(directory, config["teacher"])
    provider_status = trajectory.get("provider_error", {}).get("http_status")
    if provider_status:
        errors.append("provider_http_error")
    termination = trajectory.get("termination_reason", "worker_interrupted")
    if execution_error or not trajectory:
        errors.append("worker_environment")
    elif termination == "evaluation_error":
        errors.append("evaluation_runtime")
    elif termination == "interrupted_batch":
        errors.append("tool_batch_interrupted")
    elif trajectory.get("status") != "valid" and not errors:
        errors.append("worker_environment")
    if trajectory.get("harness", {}).get("rejections", 0):
        errors.append("protocol_correction")
    dispatches = [read_json(p) for p in (directory / "dispatches").glob("*.json")]
    business_errors = sum(public_tool_error(d["content"]) for d in dispatches)
    if business_errors:
        errors.append("business_api_error")
    if trajectory.get("status") == "valid" and trajectory.get("reward") == 0:
        errors.append("official_task_failure")
    if "budget" in termination:
        errors.append("execution_budget_exhausted")
    if result.get("status") == "DEFERRED":
        errors.append("training_format_deferred")
    if result.get("status") == "FILTERED":
        errors.append("demonstration_filter")
    if result.get("status") == "REPLAY_UNVERIFIED":
        errors.append("snapshot_replay_unverified")

    diagnostic = None
    # Upstream auto-saves after execute. Diagnose that state without replaying
    # actions, editing the old reward, or pretending it is a certified checkpoint.
    dbs = (
        Path(config["appworld_root"])
        / "experiments/outputs"
        / directory.name
        / "tasks"
        / row["task_id"]
        / "dbs"
    )
    if trajectory.get("status") != "valid" and dbs.is_dir():
        before = files_manifest(dbs)
        write_json(review_home / "autosaved_files_before.json", before)
        try:
            fresh.run_child(
                "score",
                review_home / "diagnostic_score",
                config=directory.with_suffix(".config.json"),
                task=row["task_id"],
                source=directory.name,
                timeout=120,
            )
            scored = read_json(review_home / "diagnostic_score/evaluation.json")
            unchanged = before == files_manifest(dbs)
            diagnostic = {
                "status": scored["status"],
                "success": scored["success"],
                "source_files_unchanged": unchanged,
                "evaluation_sha256": sha256(
                    review_home / "diagnostic_score/evaluation.json"
                ),
                "certified_recovery_checkpoint": False,
            }
            if not unchanged:
                errors.append("diagnostic_state_changed")
            if scored["status"] != "valid":
                errors.append("evaluation_runtime")
        except Exception as exc:
            diagnostic = {"status": "unavailable", "error_type": type(exc).__name__}
            errors.append("evaluation_runtime")

    review = {
        "task_id": row["task_id"],
        "scenario_id": row["scenario_id"],
        "official_split": row["official_split"],
        "directory": str(directory),
        "original_status": result.get("status", "INTERRUPTED"),
        "official_success": result.get("official_success"),
        "eligible_for_sft": result.get("eligible_for_sft", False),
        "errors": sorted(set(errors)),
        "termination_reason": termination,
        "execution_error_type": execution_error,
        "provider_http_status": provider_status,
        "usage": usage,
        "completed_tool_results": len(dispatches),
        "unresolved_tool_intents": max(
            0, len(list((directory / "call_intents").glob("*.json"))) - len(dispatches)
        ),
        "business_api_errors": business_errors,
        "post_stop_diagnostic": diagnostic,
        "raw_result_sha256": sha256(directory / "result.json")
        if (directory / "result.json").exists()
        else None,
        "trajectory_sha256": sha256(trajectory_path)
        if trajectory_path.exists()
        else None,
        "automatic_retry": False,
    }
    write_json(review_home / "review.json", review)
    write_json(review_home / "manifest.json", {"files": files_manifest(review_home)})
    return review


def read_review(path):
    expected = read_json(path / "manifest.json")["files"]
    actual = files_manifest(path)
    actual.pop("manifest.json")
    if actual != expected:
        raise ValueError("Review evidence changed")
    review = read_json(path / "review.json")
    source = Path(review["directory"])
    for name, key in (
        ("result.json", "raw_result_sha256"),
        ("trajectory.json", "trajectory_sha256"),
    ):
        if review[key] and sha256(source / name) != review[key]:
            raise ValueError("Reviewed source changed")
    return review


def inherited_records(config, rows):
    prior = config["parallel_collection"]["prior"]
    home = Path(prior["output"])
    if sha256(home / "campaign.json") != prior["campaign_sha256"]:
        raise ValueError("Prior campaign changed")
    records = fresh.result_rows(read_json(home / "campaign.json"), rows, home)
    found = {r["task_id"]: r for r in records if r["attempt_count"]}
    if set(found) != set(prior["results"]):
        raise ValueError("Prior task coverage changed")
    for task, record in found.items():
        directory = home / "attempts" / ("fresh_" + record["attempt_id"][:20])
        if sha256(directory / "result.json") != prior["results"][task]:
            raise ValueError("Prior result changed")
        record["source_directory"] = str(directory)
    continuation = config["parallel_collection"].get("continuation_reviews")
    if continuation:
        previous = Path(continuation["output"])
        if sha256(previous / "campaign.json") != continuation["campaign_sha256"]:
            raise ValueError("Continuation campaign changed")
        allowed = {r["task_id"]: r for r in rows}
        for task, expected in continuation["reviews"].items():
            home = previous / "reviews" / task
            if task not in allowed or sha256(home / "review.json") != expected:
                raise ValueError("Continuation review identity changed")
            review = read_review(home)
            if any(
                review[k] != allowed[task][k]
                for k in ("task_id", "scenario_id", "official_split")
            ):
                raise ValueError("Continuation task provenance changed")
            source = Path(review["directory"])
            if task in found and found[task]["source_directory"] != str(source):
                raise ValueError("Continuation replaces a previously attempted task")
            found[task] = {
                "source_directory": str(source),
                "sealed_review_home": str(home),
            }
    return found


def stop_reason(review, consecutive, *, collect_transport_errors=False):
    errors = set(review["errors"])
    if review["provider_http_status"] in {401, 402, 403}:
        return "provider_auth_or_funding", consecutive
    if errors & {"provider_model_identity", "diagnostic_state_changed"}:
        return "untrusted_service_or_state", consecutive
    if (
        collect_transport_errors
        and "provider_usage_unknown" in errors
        and "worker_environment" not in errors
    ):
        # User requested one attempt per task and a consolidated error register.
        # Keep unknown cost; never retry this request/task. Other tasks may run.
        return None, 0
    # A local task, evaluation, format, or complete over-limit receipt does not
    # stop other tasks. Repeated unavailable service/worker still trips a fuse.
    consecutive = (
        consecutive + 1
        if errors & {"provider_usage_unknown", "worker_environment"}
        else 0
    )
    return ("consecutive_service_failures" if consecutive >= 2 else None), consecutive


def run_one(config, row, directory, identity, credentials, reviews):
    failure = None
    result = {}
    try:
        fresh.run_child(
            "worker",
            directory,
            config=directory.with_suffix(".config.json"),
            task=row["task_id"],
            credentials=credentials,
        )
        result = fresh.finish_attempt(config, row, directory, identity)
    except Exception as exc:
        failure = type(exc).__name__  # No credentials/provider exception strings.
    try:
        return audit_attempt(
            config, row, directory, reviews / row["task_id"], result, failure
        )
    except Exception as exc:
        # Even a malformed/killed local receipt must not lose the task from the
        # error register or license a paid retry. Preserve a full unknown reserve.
        home = reviews / row["task_id"]
        home.mkdir(parents=True, exist_ok=True)
        review = {
            "task_id": row["task_id"],
            "scenario_id": row["scenario_id"],
            "official_split": row["official_split"],
            "directory": str(directory),
            "original_status": "AUDIT_ERROR",
            "official_success": None,
            "eligible_for_sft": False,
            "errors": ["worker_environment", "receipt_audit_error"],
            "provider_http_status": None,
            "execution_error_type": type(exc).__name__,
            "usage": {
                "requests": len(
                    list((directory / "requests").glob("*.reservation.json"))
                ),
                "inputs": 0,
                "outputs": 0,
                "cached": 0,
                "cost": 0.0,
                "unknown_cost": config["remaining_cost"],
                "counts_are_lower_bounds": True,
            },
            "completed_tool_results": len(
                list((directory / "dispatches").glob("*.json"))
            ),
            "raw_result_sha256": sha256(directory / "result.json")
            if (directory / "result.json").exists()
            else None,
            "trajectory_sha256": sha256(directory / "trajectory.json")
            if (directory / "trajectory.json").exists()
            else None,
            "automatic_retry": False,
        }
        # A final review was never committed when audit_attempt raises.
        if (home / "review.json").exists():
            raise
        write_json(home / "review.json", review)
        write_json(home / "manifest.json", {"files": files_manifest(home)})
        return review


def collect(config_path, output, credentials_file, max_new=None):
    if max_new is not None and (type(max_new) is not int or max_new <= 0):
        raise ValueError("max_new must be positive")
    config, rows = fresh.campaign_config(config_path)
    settings = config["parallel_collection"]
    if type(settings["workers"]) is not int or not 1 <= settings["workers"] <= 3:
        raise ValueError("Parallel collection permits one to three isolated workers")
    output.mkdir(parents=True, exist_ok=True)
    with (output / "campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        anchor = output / "campaign.json"
        if anchor.exists():
            if read_json(anchor) != config:
                raise ValueError("Immutable campaign changed")
        else:
            write_json(anchor, config)
        if (output / "global_stop.json").exists():
            return  # Never silently resume a global service stop.
        checked = fresh.provider_preflight(config, read_credentials(credentials_file))
        if checked["status"] == "BLOCKED":
            raise ValueError(
                "Teacher preflight blocked: " + ",".join(checked["reasons"])
            )
        fresh.environment_check()
        prior = inherited_records(config, rows)
        reviews = output / "reviews"
        done = {}
        # Complete read-only diagnostics for inherited tasks, never pay again.
        for row in rows:
            task = row["task_id"]
            path = reviews / task
            if (path / "manifest.json").exists():
                done[task] = read_review(path)
            elif task in prior:
                record = prior[task]
                if "sealed_review_home" in record:
                    shutil.copytree(record["sealed_review_home"], path)
                    done[task] = read_review(path)
                    continue
                directory = Path(record["source_directory"])
                done[task] = audit_attempt(
                    read_json(directory.with_suffix(".config.json")),
                    row,
                    directory,
                    path,
                    record,
                )
        current = fresh.result_rows(config, rows, output)
        for row, record in zip(rows, current):
            if record["attempt_count"] and row["task_id"] not in done:
                # A claimed worker is never relaunched, including controller crash.
                directory = output / "attempts" / ("fresh_" + record["attempt_id"][:20])
                path = reviews / row["task_id"]
                if path.exists():
                    raise ValueError(
                        "Interrupted review needs offline reconciliation; no paid retry"
                    )
                done[row["task_id"]] = audit_attempt(
                    read_json(directory.with_suffix(".config.json")),
                    row,
                    directory,
                    path,
                    record,
                )
        pending_rows = iter(r for r in rows if r["task_id"] not in done)
        submitted, consecutive, stop = 0, 0, None
        with ThreadPoolExecutor(max_workers=settings["workers"]) as pool:
            active = {}
            while True:
                while (
                    not stop
                    and len(active) < settings["workers"]
                    and (max_new is None or submitted < max_new)
                ):
                    row = next(pending_rows, None)
                    if row is None:
                        break
                    spent = {
                        "requests": sum(r["usage"]["requests"] for r in done.values()),
                        "outputs": sum(r["usage"]["outputs"] for r in done.values()),
                        "cost": sum(
                            r["usage"]["cost"] + r["usage"]["unknown_cost"]
                            for r in done.values()
                        ),
                    }
                    for reservation in active.values():
                        for name in spent:
                            spent[name] += reservation[name]
                    worker = fresh.worker_config(config, row, spent)
                    if (
                        min(
                            worker["remaining_requests"],
                            worker["remaining_generated_tokens"],
                            worker["remaining_cost"],
                        )
                        <= 0
                    ):
                        stop = "campaign_budget_exhausted"
                        break
                    authorize_task(
                        worker["appworld_root"],
                        row["task_id"],
                        worker["custom_research"],
                    )
                    identity = digest([digest(config), row["task_id"], 1])
                    directory = output / "attempts" / ("fresh_" + identity[:20])
                    cfg = directory.with_suffix(".config.json")
                    write_json(cfg, worker)
                    write_json(
                        directory.with_suffix(".claim.json"),
                        {
                            "attempt_id": identity,
                            "task_id": row["task_id"],
                            "config_sha256": sha256(cfg),
                            "automatic_paid_retry": False,
                        },
                    )
                    future = pool.submit(
                        run_one,
                        worker,
                        row,
                        directory,
                        identity,
                        credentials_file,
                        reviews,
                    )
                    active[future] = {
                        "requests": worker["remaining_requests"],
                        "outputs": worker["remaining_generated_tokens"],
                        "cost": worker["remaining_cost"],
                    }
                    submitted += 1
                if not active:
                    break
                finished, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in finished:
                    active.pop(future)
                    review = future.result()
                    done[review["task_id"]] = review
                    reason, consecutive = stop_reason(
                        review,
                        consecutive,
                        collect_transport_errors=settings["revision"]
                        == "fresh_parallel_error_collection_v2_20261028",
                    )
                    stop = stop or reason
                    write_json(
                        output / "progress" / (review["task_id"] + ".json"),
                        {
                            "completed": len(done),
                            "task_id": review["task_id"],
                            "status": review["original_status"],
                            "errors": review["errors"],
                        },
                    )
                    print(
                        f"{len(done)}/244 {review['task_id']} {review['original_status']} {','.join(review['errors'])}",
                        flush=True,
                    )
                if stop and not (output / "global_stop.json").exists():
                    write_json(
                        output / "global_stop.json",
                        {
                            "reason": stop,
                            "already_dispatched": len(active),
                            "completed": len(done),
                        },
                    )
        return done


def main():
    os.environ.update(
        HF_HOME=str(ROOT / ".cache/huggingface"),
        HF_HUB_OFFLINE="1",
        CUDA_VISIBLE_DEVICES="",
        OMP_NUM_THREADS="1",
        TOKENIZERS_PARALLELISM="false",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--credentials-file")
    parser.add_argument("--max-new", type=int)
    args = parser.parse_args()
    collect(args.config, args.output, args.credentials_file, args.max_new)


if __name__ == "__main__":
    main()
