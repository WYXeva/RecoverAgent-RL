"""One authorized official-API attempt for each frozen failed/unknown A task.

Reuses the existing worker, scoring, restore checks and tokenizer. The original
round is read-only; this queue never inherits an old result as its new outcome.
"""

import argparse
import fcntl
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from . import fresh_teacher as fresh
from .demonstrations import sha256
from .io import digest, read_json, write_json
from .parallel_teacher import audit_attempt, read_review, run_one, stop_reason
from .protocol_lock import verify_lock
from .research_access import ROOT, a_tasks, authorize_task
from .teacher import read_credentials

DEFAULT = ROOT / "configs/full732_official_retry_pooled_20261029.json"


def load_config(path):
    config = read_json(path)
    verify_lock(config)
    selection = config["retry_selection"]
    source = Path(selection["source_report"])
    if sha256(source) != selection["source_report_sha256"]:
        raise ValueError("Original selection report changed")
    previous = read_json(source)
    _, frozen = a_tasks()
    allowed = {r["task_id"]: r for r in frozen}
    records = previous["rows"]
    if (
        len(records) != 244
        or {r["task_id"] for r in records} != set(allowed)
        or previous["summary"]["attempted_tasks"] != 244
        or previous["global_stop"] is not None
    ):
        raise ValueError("Incomplete original A244 evidence")
    chosen = [r for r in records if r["status"] in {"FAILED", "UNKNOWN"}]
    if len(chosen) != 134 or [r["task_id"] for r in chosen] != selection["tasks"]:
        raise ValueError("Retry must include exactly all40 failed and94 unknown tasks")
    original = Path(selection["source_campaign"])
    if sha256(original / "campaign.json") != selection["source_campaign_sha256"]:
        raise ValueError("Original campaign changed")
    rows = []
    for record in chosen:
        task = record["task_id"]
        review_home = original / "reviews" / task
        review = read_review(review_home)
        if (
            sha256(review_home / "review.json") != record["review_sha256"]
            or review["original_status"] != record["status"]
            or review["official_success"] != record["official_success"]
            or review["eligible_for_sft"]
            or any(
                record[k] != allowed[task][k] for k in ("scenario_id", "official_split")
            )
        ):
            raise ValueError("Retry provenance/result differs from sealed source")
        rows.append(allowed[task])
    settings, teacher = config["fresh_campaign"], config["teacher"]
    if (
        settings["tasks"] != selection["tasks"]
        or settings["max_attempts_per_task"] != 1
        or settings["total_requests"] != len(rows) * 80
        or settings["total_generated_tokens"] != len(rows) * 16384
        or settings["total_cost_cap"] != len(rows) * config["limits"]["episode_cost"]
        or teacher["base_url"] != "https://api.deepseek.com"
        or teacher["allowed_host"] != "api.deepseek.com"
        or teacher["api_key_env"] != "DEEPSEEK_API_KEY1"
        or teacher["model"] != "deepseek-flash"
        or teacher.get("response_model_policy") is not None
        or config["limits"]["episode_requests"] != 80
        or config["limits"]["episode_wall_seconds"] != 600
    ):
        raise ValueError("Unfrozen official retry scope/provider/budget")
    return config, rows


def inherit_reviews(config, rows, output):
    prior = config.get("official_continuation")
    if prior is None:
        return
    source = Path(prior["campaign"])
    if sha256(source / "campaign.json") != prior["campaign_sha256"]:
        raise ValueError("Official continuation campaign changed")
    allowed = {r["task_id"]: r for r in rows}
    for task, expected in prior["reviews"].items():
        home = source / "reviews" / task
        if task not in allowed or sha256(home / "review.json") != expected:
            raise ValueError("Official continuation review changed")
        record = read_review(home)
        if any(
            record[k] != allowed[task][k]
            for k in ("task_id", "scenario_id", "official_split")
        ):
            raise ValueError("Official continuation identity changed")
        target = output / "reviews" / task
        if target.exists():
            if read_review(target) != record:
                raise ValueError("Official continuation replaced old result")
        else:
            shutil.copytree(home, target)


def official_credentials(config, path):
    """Select only the explicitly authorized second credential group."""
    credentials = read_credentials(path)
    base = urlsplit(credentials.get("DEEPSEEK_BASE_URL1", ""))
    if (
        not credentials.get("DEEPSEEK_API_KEY1")
        or base.scheme != "https"
        or base.netloc != "api.deepseek.com"
        or base.path.rstrip("/") not in {"", "/v1"}
        or base.query
        or base.fragment
        or credentials.get("DEEPSEEK_MODEL1") != config["teacher"]["model"]
    ):
        raise ValueError("Official second credential group missing or mismatched")
    # The frozen worker checks these historical metadata names. Map metadata
    # only inside this dedicated process; the key is never copied or renamed.
    credentials["DEEPSEEK_BASE_URL"] = config["teacher"]["base_url"]
    credentials["DEEPSEEK_MODEL"] = config["teacher"]["model"]
    return credentials


def collect(config_path, output, credentials_file, max_new=None):
    if max_new is not None and (type(max_new) is not int or max_new <= 0):
        raise ValueError("max_new must be positive")
    config, rows = load_config(config_path)
    credentials = official_credentials(config, credentials_file)
    if fresh.provider_preflight(config, credentials)["status"] == "BLOCKED":
        raise ValueError("Official Teacher preflight blocked")
    fresh.environment_check()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        anchor = output / "campaign.json"
        if anchor.exists():
            if read_json(anchor) != config:
                raise ValueError("Immutable retry campaign changed")
        else:
            write_json(anchor, config)
        if (output / "global_stop.json").exists():
            return
        inherit_reviews(config, rows, output)
        previous_env = {
            k: os.environ.get(k) for k in ("DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")
        }
        os.environ.update({k: credentials[k] for k in previous_env})
        try:
            done, submitted, consecutive = {}, 0, 0
            for row, existing in zip(rows, fresh.result_rows(config, rows, output)):
                task = row["task_id"]
                identity = digest([digest(config), task, 1])
                directory = output / "attempts" / ("fresh_" + identity[:20])
                home = output / "reviews" / task
                if (home / "manifest.json").exists():
                    done[task] = read_review(home)
                    continue
                if existing["attempt_count"]:
                    # A claimed paid attempt is never restarted after interruption.
                    done[task] = audit_attempt(
                        read_json(directory.with_suffix(".config.json")),
                        row,
                        directory,
                        home,
                        existing,
                    )
                    continue
                if (output / "stop_requested.json").exists():
                    break
                if max_new is not None and submitted >= max_new:
                    break
                spent = {
                    "requests": sum(r["usage"]["requests"] for r in done.values()),
                    "outputs": sum(r["usage"]["outputs"] for r in done.values()),
                    "cost": sum(
                        r["usage"]["cost"] + r["usage"]["unknown_cost"]
                        for r in done.values()
                    ),
                }
                worker = fresh.worker_config(config, row, spent)
                if (
                    min(
                        worker["remaining_requests"],
                        worker["remaining_generated_tokens"],
                        worker["remaining_cost"],
                    )
                    <= 0
                ):
                    write_json(
                        output / "global_stop.json",
                        {"reason": "campaign_budget_exhausted", "completed": len(done)},
                    )
                    break
                authorize_task(worker["appworld_root"], task, worker["custom_research"])
                cfg = directory.with_suffix(".config.json")
                write_json(cfg, worker)
                write_json(
                    directory.with_suffix(".claim.json"),
                    {
                        "attempt_id": identity,
                        "task_id": task,
                        "config_sha256": sha256(cfg),
                        "automatic_paid_retry": False,
                        "authorized_round": 2,
                    },
                )
                review = run_one(
                    worker,
                    row,
                    directory,
                    identity,
                    credentials_file,
                    output / "reviews",
                )
                done[task] = review
                submitted += 1
                write_json(
                    output / "progress" / (task + ".json"),
                    {
                        "completed": len(done),
                        "planned": len(rows),
                        "task_id": task,
                        "status": review["original_status"],
                        "errors": review["errors"],
                    },
                )
                print(
                    f"{len(done)}/{len(rows)} {task} {review['original_status']} {','.join(review['errors'])}",
                    flush=True,
                )
                reason, consecutive = stop_reason(
                    review, consecutive, collect_transport_errors=True
                )
                if reason:
                    write_json(
                        output / "global_stop.json",
                        {"reason": reason, "completed": len(done)},
                    )
                    break
            return done
        finally:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def main():
    os.environ.update(
        HF_HOME=str(ROOT / ".cache/huggingface"),
        HF_HUB_OFFLINE="1",
        CUDA_VISIBLE_DEVICES="",
        OMP_NUM_THREADS="1",
    )
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=DEFAULT)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--credentials-file", default=".env")
    p.add_argument("--max-new", type=int)
    a = p.parse_args()
    collect(a.config, a.output, a.credentials_file, a.max_new)


if __name__ == "__main__":
    main()
