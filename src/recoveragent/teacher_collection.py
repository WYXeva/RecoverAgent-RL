"""One train-only Teacher top-up campaign, reusing the real executor and SFT masks."""

import argparse
import copy
import fcntl
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from .demonstrations import as_demo, filter_attempt, sha256
from .io import digest, files_manifest, read_json, write_json
from .v2_tasks import ROOT, context_for, plan

REVISION = "v2_teacher_collection_64k_quarantine_20261111"
CONFIG = ROOT / "configs/teacher_collection_64k_quarantine_20261111.json"
LOCK = ROOT / "configs/teacher_collection_64k_quarantine_20261111_lock.json"
QUARANTINE = "unparseable_numeric_answer_excluded_v1"


def configuration():
    return read_json(CONFIG)


def verify_lock():
    lock = read_json(LOCK)
    required = {
        str(p.relative_to(ROOT)) for p in (ROOT / "src/recoveragent").glob("*.py")
    }
    required |= {
        str(CONFIG.relative_to(ROOT)),
        "configs/v2_scenario_split_20261109.json",
        "scripts/collect_teacher_64k.py",
    }
    if lock.get("revision") != REVISION or not required <= lock["files"].keys():
        raise ValueError("Incomplete Teacher collection source lock")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("Teacher collection source changed: " + name)
    return sha256(LOCK)


def worker_config(task, run_id):
    cfg = configuration()
    value = copy.deepcopy(read_json(ROOT / cfg["template"])["teacher_config"])
    value.pop("recovery_protocol")
    value.update(
        v2_runtime=REVISION,
        collection_run_id=run_id,
        custom_research=context_for(task, training=True),
        appworld_root=str(ROOT / cfg["appworld_root"]),
        student=cfg["student"],
        teacher=copy.deepcopy(cfg["teacher"]),
        collection_context=cfg["context"],
        protocol_lock=str(LOCK.relative_to(ROOT)),
        remaining_requests=80,
        remaining_generated_tokens=16384,
        remaining_cost=cfg["episode_cost_cap_usd"],
    )
    value["teacher"]["opencode_session_id"] = (
        "ses_" + digest([REVISION, run_id, task])[:32]
    )
    value["authorization"] = {
        "approved": True,
        "reference": "User requested 64k Teacher collection to 256 cumulative successes including existing data; existing Go subscription only.",
        "currency": "USD",
        "max_cost": cfg["episode_cost_cap_usd"],
    }
    value["limits"]["episode_cost"] = cfg["episode_cost_cap_usd"]
    return value


def validate_worker_config(config):
    verify_lock()
    expected = worker_config(
        config["custom_research"]["task_id"], config["collection_run_id"]
    )
    if config != expected:
        raise ValueError("Teacher collection worker differs from frozen configuration")


def selection(rows, excluded):
    """Visit one variant per scenario before a second, retaining frozen scene order."""
    scenes = {}
    for row in rows:
        if row["role"] == "train":
            scenes.setdefault(row["scenario_id"], []).append(row["task_id"])
    return [
        tasks[i]
        for i in range(3)
        for tasks in scenes.values()
        if tasks[i] not in excluded
    ]


def encode_sample(sample, tokenizer, max_length):
    from .base_sft import assistant_examples

    context = context_for(sample["task_id"], training=True)
    rows = assistant_examples(sample, tokenizer, max_length=max_length)
    if not rows or any(sum(row["loss_mask"]) > 4096 for row in rows):
        raise ValueError("Assistant target too long or empty; no truncation")
    for row in rows:
        row["v2_context"] = context
        row["example_weight"] = 1.0
        if any(label != -100 for label in row["labels"][: row["target_start"]]):
            raise ValueError("Unexpected prefix supervision")
    return rows


def context_receipt(tokenizer, messages, tools, max_tokens, context, identity):
    from .context_compiler import encode

    receipt = {
        "input_tokens": len(encode(tokenizer, messages, tools)),
        "reserved_output_tokens": max_tokens,
        "max_sequence_tokens": context["max_sequence_tokens"],
        "tokenizer": identity["model"],
        "history_sha256": digest(messages),
        "tools_sha256": digest(tools),
        "truncated": False,
    }
    receipt["within_budget"] = (
        receipt["input_tokens"] + max_tokens <= context["max_sequence_tokens"]
    )
    return receipt


def save_sample(directory, sample, tokenizer, max_length):
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "sample.json", sample)
    result = {
        "task_id": sample["task_id"],
        "sample_sha256": sha256(directory / "sample.json"),
        "eligible": False,
    }
    try:
        rows = encode_sample(sample, tokenizer, max_length)
    except ValueError as exc:
        if "too long" not in str(exc):
            raise
        result.update(status="deferred", reason=str(exc))
    else:
        with gzip.open(directory / "examples.jsonl.gz", "wt") as stream:
            for row in rows:
                stream.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
        result.update(
            status="eligible",
            eligible=True,
            examples=len(rows),
            supervised_tokens=sum(sum(row["loss_mask"]) for row in rows),
            max_sequence_tokens=max(len(row["input_ids"]) for row in rows),
            examples_sha256=sha256(directory / "examples.jsonl.gz"),
        )
    write_json(directory / "encoding.json", result)
    return result


def prepare(output):
    from . import APPWORLD_REVISION
    from .schema_compatibility import convert_catalogue, load_public_openapi
    from .v2_model import load_tokenizer

    lock_sha = verify_lock()
    cfg, frozen = configuration(), plan()
    source = ROOT / cfg["reuse_dataset"]
    if sha256(source / "manifest.json") != cfg["reuse_manifest_sha256"]:
        raise ValueError("Original Teacher data manifest changed")
    manifest = read_json(source / "manifest.json")
    for name, expected in manifest["files"].items():
        if sha256(source / name) != expected:
            raise ValueError("Original Teacher data changed: " + name)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config.json", cfg)
    samples = [
        json.loads(line)
        for line in (source / "successes.jsonl").read_text().splitlines()
    ]
    train = {r["task_id"] for r in frozen["rows"] if r["role"] == "train"}
    samples = [s for s in samples if s["task_id"] in train]
    if len({s["task_id"] for s in samples}) != len(samples):
        raise ValueError("Duplicate historical successful task")
    catalogue = {}
    for sample in samples:
        for turn in sample["turns"]:
            for tool in turn["tools"]:
                name = tool["function"]["name"]
                if name in catalogue and catalogue[name] != tool:
                    raise ValueError("Conflicting historical tool schemas")
                catalogue[name] = tool
    documents, provenance = load_public_openapi(list(catalogue.values()))
    audit = convert_catalogue(
        list(catalogue.values()), documents, appworld_revision=APPWORLD_REVISION
    )
    write_json(output / "reuse_schema_conversion.json", {**audit, "source": provenance})
    converted = {t["function"]["name"]: t for t in audit["compatible_catalogue"]}
    tokenizer = load_tokenizer(cfg["student"])
    records = []
    for raw in samples:
        sample = copy.deepcopy(raw)
        for turn in sample["turns"]:
            turn["tools"] = [converted[t["function"]["name"]] for t in turn["tools"]]
        entry = save_sample(
            output / "reused" / raw["task_id"],
            sample,
            tokenizer,
            cfg["context"]["max_sequence_tokens"],
        )
        records.append(
            {
                **entry,
                "source_demo_sha256": digest(raw),
                "source_manifest_sha256": cfg["reuse_manifest_sha256"],
            }
        )
        print(
            json.dumps(
                {"event": "reuse", "task_id": raw["task_id"], "status": entry["status"]}
            ),
            flush=True,
        )
    baseline_reused_eligible = sum(r["eligible"] for r in records)
    excluded = {r["task_id"] for r in records}
    prior_attempts = []
    for item in cfg.get("prior_attempt_results", []):
        path = ROOT / item["path"]
        if sha256(path) != item["sha256"]:
            raise ValueError("Prior attempt receipt changed")
        prior = read_json(path)
        context_for(prior["task_id"], training=True)
        if prior["task_id"] in excluded:
            raise ValueError("Duplicate prior task attempt")
        excluded.add(prior["task_id"])
        if item.get("attempt_directory"):
            directory = ROOT / item["attempt_directory"]
            for name in ("trajectory", "evaluation"):
                if sha256(directory / (name + ".json")) != prior[name + "_sha256"]:
                    raise ValueError("Prior attempt evidence changed")
            prior = process_attempt(
                directory,
                output / "reused" / prior["task_id"],
                read_json(directory.with_suffix(".config.json")),
                tokenizer,
            )
            write_json(output / "prior_results" / (prior["task_id"] + ".json"), prior)
            if prior["official_success"]:
                records.append({**prior, "prior_attempt_source": item})
        prior_attempts.append({**item, **prior})
    chosen = selection(frozen["rows"], excluded)
    result = {
        "revision": REVISION,
        "source_lock_sha256": lock_sha,
        "split_manifest_sha256": frozen["manifest_sha256"],
        "reuse": records,
        "prior_attempts": prior_attempts,
        "reused_eligible": sum(r["eligible"] for r in records),
        "baseline_reused_eligible": baseline_reused_eligible,
        "target_successes": cfg["target_successes"],
        "tasks": chosen,
        "selection": "round-robin variants within frozen train scenario order; exclude all already-successful source tasks",
        "max_attempts_per_task": 1,
        "training_started": False,
    }
    write_json(output / "campaign.json", result)
    print(
        json.dumps(
            {
                "event": "prepared",
                "reused_eligible": result["reused_eligible"],
                "remaining_tasks": len(chosen),
            }
        ),
        flush=True,
    )
    return result


def worker(task, run_id, directory, credentials_file):
    from .p3a import run_teacher_episode
    from .teacher import read_credentials
    from .v2_model import load_tokenizer

    cfg = worker_config(task, run_id)
    credentials = read_credentials(credentials_file)
    if (
        credentials.get("DEEPSEEK_BASE_URL", "").rstrip("/")
        != cfg["teacher"]["base_url"]
        or credentials.get("DEEPSEEK_MODEL") != cfg["teacher"]["model"]
    ):
        raise ValueError("Go endpoint/model differs from frozen configuration")
    write_json(directory.with_suffix(".config.json"), cfg)
    run_teacher_episode(cfg, task, directory, credentials)
    result = process_attempt(
        directory, directory / "encoded", cfg, load_tokenizer(cfg["student"])
    )
    write_json(directory / "result.json", result)
    return result


def process_attempt(directory, encoded_directory, cfg, tokenizer):
    """Filter durable evidence without re-execution or rewriting the source result."""
    record = read_json(directory / "trajectory.json")
    task = record["task_id"]
    evaluation = (
        read_json(directory / "evaluation.json")
        if (directory / "evaluation.json").exists()
        else None
    )
    reasons = (
        filter_attempt(record, evaluation, {task}, cfg["limits"], directory)
        if record["status"] == "valid"
        else ["infrastructure_error", record.get("termination_reason", "unknown")]
    )
    result = {
        "task_id": task,
        "official_success": record["reward"] == 1
        if record["status"] == "valid"
        else None,
        "eligible": False,
        "status": "failed",
        "reasons": reasons,
        "termination": record.get("termination_reason"),
        "usage": record["usage"],
        "provider_error": record.get("provider_error"),
        "trajectory_sha256": sha256(directory / "trajectory.json"),
        "evaluation_sha256": sha256(directory / "evaluation.json")
        if evaluation
        else None,
    }
    if not reasons:
        sample = as_demo(record)
        result.update(
            save_sample(
                encoded_directory,
                sample,
                tokenizer,
                cfg["collection_context"]["max_sequence_tokens"],
            )
        )
    elif record["status"] != "valid":
        result["status"] = "infrastructure_error"
        quarantine = numeric_answer_quarantine(record, directory)
        if quarantine:
            result["task_error_quarantine"] = quarantine
    elif result["official_success"]:
        result["status"] = "filtered"
    return result


def numeric_answer_quarantine(record, directory):
    """Scheduling only: keep an unscoreable answer unknown and out of all training.

    Recognize the two observed official numeric-answer conversion failures. This
    does not change evaluation.py, any reward, or the success filtering policy.
    """
    path = directory / "isolated_evaluation.json"
    if (
        record.get("termination_reason") != "evaluation_error"
        or record.get("failure_category") != "evaluation_runtime"
        or record.get("provider_error")
        or not path.exists()
    ):
        return None
    evaluated = read_json(path)
    official = evaluated.get("official", {})
    failures = official.get("failures", [])
    if (
        evaluated.get("status") != "evaluation_error"
        or evaluated.get("category") != "unknown"
        or evaluated.get("success") is not None
        or evaluated.get("official_success") is not False
        or evaluated.get("official_result_complete") is not True
        or evaluated.get("environment", {}).get("uri_roundtrip") is not True
        or evaluated.get("observations")
        != [{"exception_type": "ValueError", "category": "unknown_exception"}]
        or official.get("success") is not False
        or official.get("num_tests") != len(official.get("passes", [])) + len(failures)
        or len(failures) != 1
        or failures[0].get("requirement", "").strip() != "assert answers match."
        or not any(
            marker in failures[0].get("trace", "")
            for marker in (
                "normalize=float",
                "predicted_answer = float(predicted_answer)",
            )
        )
    ):
        return None
    calls = [
        c
        for m in record.get("messages", [])
        for c in m.get("tool_calls", [])
        if c.get("function", {}).get("name") == "supervisor__complete_task"
    ]
    if not calls:
        return None
    answer = json.loads(calls[-1]["function"]["arguments"]).get("answer")
    if not isinstance(answer, str):
        return None
    try:
        float(answer)
    except ValueError:
        return {
            "protocol": QUARANTINE,
            "evaluation_sha256": sha256(path),
            "action": "exclude_task_without_retry_or_score_override",
        }
    return None


def blocks_campaign(result):
    return result["status"] in {
        "worker_error",
        "worker_timeout",
        "infrastructure_error",
    } and not (
        result["status"] == "infrastructure_error"
        and result.get("task_error_quarantine", {}).get("protocol") == QUARANTINE
        and result.get("termination") == "evaluation_error"
        and result.get("official_success") is None
        and result.get("eligible") is False
        and not result.get("provider_error")
    )


def run_worker(output, task, credentials):
    directory = output / "attempts" / (output.name + "_" + task)
    claim = output / "claims" / (task + ".json")
    write_json(
        claim, {"task_id": task, "claimed_epoch": time.time(), "automatic_retry": False}
    )
    command = [
        sys.executable,
        "-m",
        "recoveragent.teacher_collection",
        "worker",
        "--task",
        task,
        "--run-id",
        output.name,
        "--output",
        str(directory),
        "--credentials",
        str(credentials),
    ]
    directory.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with directory.with_suffix(".log").open("x") as log:
        try:
            completed = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=1500,
                check=False,
                start_new_session=True,
            )
            result = (
                read_json(directory / "result.json")
                if (directory / "result.json").exists()
                else {
                    "task_id": task,
                    "status": "worker_error",
                    "eligible": False,
                    "exit_code": completed.returncode,
                }
            )
        except subprocess.TimeoutExpired:
            result = {"task_id": task, "status": "worker_timeout", "eligible": False}
    if "usage" not in result:
        from .p3a import interrupted_usage

        result["usage"] = interrupted_usage(directory, configuration()["teacher"])
    result["worker_wall_seconds"] = time.monotonic() - started
    write_json(output / "results" / (task + ".json"), result)
    return result


def summary(output, campaign, results, active, status):
    cfg = configuration()
    prior = campaign.get("prior_attempts", [])
    all_results = prior + results
    value = {
        "revision": REVISION,
        "status": status,
        "updated_epoch": time.time(),
        "target_successes": cfg["target_successes"],
        "reused_eligible": campaign["reused_eligible"],
        "new_attempts_finished": len(results),
        "prior_attempts_finished": len(prior),
        "all_attempts_finished": len(all_results),
        "active_tasks": active,
        "new_official_successes": sum(
            r.get("official_success") is True for r in results
        ),
        "all_official_successes": sum(
            r.get("official_success") is True for r in all_results
        ),
        "baseline_reused_eligible": campaign.get(
            "baseline_reused_eligible", campaign["reused_eligible"]
        ),
        "new_eligible": sum(r["eligible"] for r in results),
        "total_eligible": campaign["reused_eligible"]
        + sum(r["eligible"] for r in results),
        "new_deferred": sum(r["status"] == "deferred" for r in results),
        "new_infrastructure_errors": sum(
            r["status"] in {"worker_error", "worker_timeout", "infrastructure_error"}
            for r in results
        ),
        "quarantined_task_errors": sum(
            bool(r.get("task_error_quarantine")) for r in all_results
        ),
        "usage": {
            k: sum(r.get("usage", {}).get(k, 0) for r in all_results)
            for k in [
                "provider_requests",
                "prompt_tokens",
                "generated_tokens",
                "cached_prompt_tokens",
                "usage_priced_cost",
                "unknown_cost_reservation",
            ]
        },
        "pricing_basis": "USD peak-rate allowance estimate, not a subscription invoice or account debit",
        "max_sequence_tokens": cfg["context"]["max_sequence_tokens"],
        "training_started": False,
    }
    temporary = output / "progress.tmp"
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output / "progress.json")
    return value


def collect(output, credentials):
    verify_lock()
    campaign, cfg = read_json(output / "campaign.json"), configuration()
    if (
        campaign["source_lock_sha256"] != sha256(LOCK)
        or read_json(output / "config.json") != cfg
    ):
        raise ValueError("Campaign configuration/source changed")
    output.mkdir(parents=True, exist_ok=True)
    with (output / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (output / "collector.pid").write_text(str(os.getpid()) + "\n")
        results = [read_json(p) for p in sorted((output / "results").glob("*.json"))]
        claims = {p.stem for p in (output / "claims").glob("*.json")}
        resolved = {r["task_id"] for r in results}
        if claims - resolved:
            summary(
                output,
                campaign,
                results,
                sorted(claims - resolved),
                "blocked_unresolved_prior_claim",
            )
            return
        pending = iter(t for t in campaign["tasks"] if t not in claims)
        active, exhausted, stopped = {}, False, None
        with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as pool:
            while True:
                count = campaign["reused_eligible"] + sum(
                    r["eligible"] for r in results
                )
                if (output / "STOP").exists():
                    stopped = "stopped_by_control_file"
                spent = sum(
                    r.get("usage", {}).get("usage_priced_cost", 0)
                    + r.get("usage", {}).get("unknown_cost_reservation", 0)
                    for r in campaign.get("prior_attempts", []) + results
                )
                if (
                    spent + (len(active) + 1) * cfg["episode_cost_cap_usd"]
                    > cfg["total_cost_cap_usd"]
                ):
                    stopped = "cost_cap"
                while (
                    not stopped
                    and not exhausted
                    and len(active) < (cfg["concurrency"] if results else 1)
                    and count + len(active) < cfg["target_successes"]
                    and spent + (len(active) + 1) * cfg["episode_cost_cap_usd"]
                    <= cfg["total_cost_cap_usd"]
                ):
                    task = next(pending, None)
                    if task is None:
                        exhausted = True
                        break
                    future = pool.submit(run_worker, output, task, credentials)
                    active[future] = task
                    print(
                        json.dumps(
                            {
                                "event": "started",
                                "task_id": task,
                                "total_eligible": count,
                            }
                        ),
                        flush=True,
                    )
                summary(
                    output,
                    campaign,
                    results,
                    list(active.values()),
                    "running"
                    if active
                    else stopped
                    or (
                        "target_reached"
                        if count >= cfg["target_successes"]
                        else "pool_exhausted"
                    ),
                )
                if not active:
                    break
                done, _ = wait(active, timeout=15, return_when=FIRST_COMPLETED)
                for future in done:
                    task = active.pop(future)
                    result = future.result()
                    results.append(result)
                    print(
                        json.dumps(
                            {
                                "event": "finished",
                                "task_id": task,
                                "status": result["status"],
                                "official_success": result.get("official_success"),
                            }
                        ),
                        flush=True,
                    )
                    if blocks_campaign(result):
                        stopped = "blocked_infrastructure"
        export(output)


def export(output):
    campaign = read_json(output / "campaign.json")
    samples, sources = [], []
    for row in campaign["reuse"]:
        if row["eligible"]:
            path = output / "reused" / row["task_id"] / "sample.json"
            samples.append(read_json(path))
            sources.append(
                {
                    "task_id": row["task_id"],
                    "kind": "reused",
                    "sample": str(path),
                    "sha256": sha256(path),
                }
            )
    for path in sorted((output / "results").glob("*.json")):
        result = read_json(path)
        if result["eligible"]:
            sample = (
                output
                / "attempts"
                / (output.name + "_" + result["task_id"])
                / "encoded/sample.json"
            )
            samples.append(read_json(sample))
            sources.append(
                {
                    "task_id": result["task_id"],
                    "kind": "new",
                    "sample": str(sample),
                    "sha256": sha256(sample),
                }
            )
    if len({s["task_id"] for s in samples}) != len(samples):
        raise ValueError("Duplicate successful task in combined pool")
    destination = output / "exports" / f"pool_{len(samples):03d}_{time.time_ns()}"
    destination.mkdir(parents=True)
    with (destination / "successes.jsonl").open("x") as stream:
        for sample in samples:
            stream.write(json.dumps(sample, ensure_ascii=False) + "\n")
    write_json(
        destination / "manifest.json",
        {
            "revision": REVISION,
            "count": len(samples),
            "max_sequence_tokens": 65536,
            "sources": sources,
            "files": files_manifest(destination),
            "training_started": False,
        },
    )
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "collect", "worker", "export"])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--credentials", type=Path, default=ROOT / ".env")
    parser.add_argument("--task")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "prepare":
        prepare(output)
    elif args.mode == "collect":
        collect(output, args.credentials.resolve())
    elif args.mode == "worker":
        worker(args.task, args.run_id, output, args.credentials)
    else:
        print(export(output))


if __name__ == "__main__":
    main()
