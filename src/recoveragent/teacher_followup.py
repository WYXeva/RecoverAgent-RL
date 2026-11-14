"""Bounded fresh demonstrations after unsuccessful S1 checkpoint recoveries.

Reuse P3-A execution, official scoring and collection encoding. Each claimed
model/task attempt runs once; restarts reconcile receipts without paid replay.
"""

import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .demonstrations import sha256
from .io import digest, files_manifest, read_json, write_json
from .v2_tasks import ROOT, context_for

REVISION = "v2_teacher_followup_format_20261114"
CONFIG = ROOT / "configs/teacher_followup_format_20261114.json"
LOCK = ROOT / "configs/teacher_followup_format_20261114_lock.json"
ESCALATION = "v2_teacher_escalation_20261114"
LOCAL_ESCALATION = "v2_teacher_escalation_local_20261114"
RUNTIMES = {REVISION, ESCALATION, LOCAL_ESCALATION}


def paths(revision=REVISION):
    if revision == REVISION:
        return CONFIG, LOCK
    if revision == ESCALATION:
        return (
            ROOT / "configs/teacher_escalation_20261114.json",
            ROOT / "configs/teacher_escalation_20261114_lock.json",
        )
    if revision == LOCAL_ESCALATION:
        return (
            ROOT / "configs/teacher_escalation_local_20261114.json",
            ROOT / "configs/teacher_escalation_local_20261114_lock.json",
        )
    raise ValueError("Unknown Teacher follow-up revision")


def configuration(revision=REVISION):
    return read_json(paths(revision)[0])


def output_revision(output):
    campaign = output / "campaign.json"
    return read_json(campaign)["revision"] if campaign.exists() else REVISION


def verify_lock(revision=REVISION):
    config_path, lock_path = paths(revision)
    cfg, lock = configuration(revision), read_json(lock_path)
    required = {
        str(p.relative_to(ROOT)) for p in (ROOT / "src/recoveragent").glob("*.py")
    }
    required |= {
        str(config_path.relative_to(ROOT)),
        "scripts/collect_teacher_followup.py",
        cfg["template"],
        cfg["source_config"],
        "configs/v2_scenario_split_20261109.json",
        "configs/unified_task_split_full_pool_candidate.json",
        "requirements/appworld-evaluation-20261113.lock.txt",
    }
    if "carry_from" in cfg:
        required |= {cfg["carry_from"]["config"], cfg["carry_from"]["source_lock"]}
    if revision in {ESCALATION, LOCAL_ESCALATION}:
        required |= {
            "scripts/run_teacher_escalation.py",
            cfg["prior_followup"]["config"],
            cfg["prior_followup"]["source_lock"],
        }
    if lock.get("revision") != revision or not required <= lock["files"].keys():
        raise ValueError("Incomplete follow-up source lock")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("Follow-up source changed: " + name)
    return sha256(lock_path)


def select_tasks(summary):
    cfg = configuration()
    source = read_json(ROOT / cfg["source_config"])
    entries = {e["task_id"]: e for e in summary["entries"]}
    if len(entries) != len(summary["entries"]):
        raise ValueError("Duplicate recovery task")
    selected = [
        task
        for task in source["feedback_task_ids"]
        if task in entries
        and entries[task].get("acceptance", {}).get("status") != "eligible"
    ]
    if selected != cfg["task_ids"] or len(selected) != len(set(selected)):
        raise ValueError("Follow-up selection differs from frozen train failures")
    for task in selected:
        context_for(task, training=True)
    return selected


def worker_config(task, run_id, root, teacher_name, *, revision=REVISION):
    cfg = configuration(revision)
    if task not in cfg["task_ids"] or teacher_name not in cfg["teacher_order"]:
        raise ValueError("Task/model is outside frozen follow-up campaign")
    value = copy.deepcopy(read_json(ROOT / cfg["template"])["teacher_config"])
    value.pop("recovery_protocol")
    teacher = copy.deepcopy(cfg["teachers"][teacher_name])
    teacher["opencode_session_id"] = (
        "ses_" + digest([revision, run_id, task, teacher_name])[:32]
    )
    value.update(
        v2_runtime=revision,
        collection_run_id=run_id,
        followup_teacher=teacher_name,
        custom_research=context_for(task, training=True),
        appworld_root=str(root),
        student=cfg["student"],
        teacher=teacher,
        collection_context=cfg["context"],
        environment_contract=cfg["environment_contract"],
        protocol_lock=str(paths(revision)[1].relative_to(ROOT)),
        remaining_requests=80,
        remaining_generated_tokens=16384,
        remaining_cost=cfg["episode_cost_cap_usd"],
    )
    value["authorization"] = {
        "approved": True,
        "currency": "USD",
        "max_cost": cfg["episode_cost_cap_usd"],
        "reference": "User authorized fresh Teacher attempts after failed recovery, and GPT fallback at the same endpoint/key; no subscription changes.",
    }
    value["limits"]["episode_cost"] = cfg["episode_cost_cap_usd"]
    return value


def validate_worker_config(config):
    revision = config["v2_runtime"]
    verify_lock() if revision == REVISION else verify_lock(revision)
    expected = worker_config(
        config["custom_research"]["task_id"],
        config["collection_run_id"],
        config["appworld_root"],
        config["followup_teacher"],
        revision=revision,
    )
    if config != expected:
        raise ValueError("Follow-up worker differs from frozen configuration")


def prepare(output, deployment):
    lock_sha = verify_lock()
    cfg = configuration()
    summary_path = Path(deployment["recovery_summary"])
    if sha256(summary_path) != cfg["source_recovery_summary_sha256"]:
        raise ValueError("Original recovery summary changed")
    selected = select_tasks(read_json(summary_path))
    previous = Path(deployment["previous_output"])
    if (
        sha256(previous / "config.json") != cfg["carry_from"]["config_sha256"]
        or read_json(previous / "campaign.json")["source_lock_sha256"]
        != cfg["carry_from"]["source_lock_sha256"]
    ):
        raise ValueError("Previous follow-up config/source changed")
    carried = {}
    for path in sorted((previous / "attempts").glob("*/result.json")):
        result = read_json(path)
        if (
            result["task_id"] not in selected
            or result["teacher"] not in cfg["teacher_order"]
        ):
            raise ValueError("Unexpected previous Teacher attempt")
        if result.get("trajectory_sha256") != sha256(path.with_name("trajectory.json")):
            raise ValueError("Previous Teacher trajectory changed")
        if result.get("evaluation_sha256") != sha256(path.with_name("evaluation.json")):
            raise ValueError("Previous Teacher official result changed")
        key = result["task_id"] + ":" + result["teacher"]
        if key in carried:
            raise ValueError("Duplicate previous Teacher model/task attempt")
        carried[key] = {"path": str(path.resolve()), "sha256": sha256(path)}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config.json", cfg)
    write_json(output / "deployment.json", deployment)
    write_json(
        output / "campaign.json",
        {
            "revision": REVISION,
            "source_lock_sha256": lock_sha,
            "source_recovery_summary_sha256": sha256(summary_path),
            "task_ids": selected,
            "start_kind": "original_initial",
            "existing_recovery_suffixes": cfg["existing_eligible_recovery_suffixes"],
            "new_data_kind": "full_teacher_demonstration_not_recovery_suffix",
            "carried_attempts": carried,
        },
    )


def process_attempt(directory, cfg):
    from .teacher_collection import process_attempt as process
    from .v2_model import load_tokenizer

    result_path = directory / "result.json"
    if result_path.exists():
        return read_json(result_path)
    # Encoding may have finished just before the worker died. It is immutable.
    encoded = directory / "encoded"
    if (encoded / "encoding.json").exists():
        encoded = directory / "encoded_reconciled"
    result = process(directory, encoded, cfg, load_tokenizer(cfg["student"]))
    result.update(
        teacher=cfg["followup_teacher"],
        start_kind="original_initial",
        recovery_suffix=False,
        encoded_directory=str(encoded),
    )
    write_json(result_path, result)
    return result


def worker(directory, credentials_file):
    from .p3a import run_teacher_episode
    from .teacher import read_credentials

    cfg = read_json(directory.with_suffix(".config.json"))
    validate_worker_config(cfg)
    credentials = read_credentials(credentials_file)
    if (
        credentials.get("DEEPSEEK_BASE_URL", "").rstrip("/")
        != cfg["teacher"]["base_url"]
    ):
        raise ValueError("Credential endpoint differs from authorized endpoint")
    write_json(
        directory.with_suffix(".worker.json"),
        {"pid": os.getpid(), "started": time.time()},
    )
    run_teacher_episode(cfg, cfg["custom_research"]["task_id"], directory, credentials)
    return process_attempt(directory, cfg)


def running_worker(directory):
    import psutil

    receipt = directory.with_suffix(".worker.json")
    if not receipt.exists():
        return False
    try:
        process = psutil.Process(read_json(receipt)["pid"])
        return (
            str(directory) in process.cmdline()
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def attempt(output, task, teacher_name, deployment):
    from .p3a import interrupted_usage

    source = (
        read_json(output / "campaign.json")
        .get("carried_attempts", {})
        .get(task + ":" + teacher_name)
    )
    if source:
        if sha256(source["path"]) != source["sha256"]:
            raise ValueError("Carried Teacher attempt changed")
        return {**read_json(source["path"]), "carried_from": source}

    directory = (output / "attempts" / f"{output.name}_{task}_{teacher_name}").resolve()
    result_path = directory / "result.json"
    cfg = worker_config(
        task,
        output.name,
        deployment["appworld_root"],
        teacher_name,
        revision=output_revision(output),
    )
    claim = directory.with_suffix(".claim.json")
    directory.parent.mkdir(parents=True, exist_ok=True)
    if not claim.exists():
        write_json(directory.with_suffix(".config.json"), cfg)
        write_json(
            claim,
            {
                "task_id": task,
                "teacher": teacher_name,
                "config_sha256": digest(cfg),
                "started": time.time(),
            },
        )
        command = [
            deployment["python"],
            "-m",
            "recoveragent.teacher_followup",
            "worker",
            "--output",
            str(directory),
            "--credentials",
            deployment["credentials"],
        ]
        with directory.with_suffix(".log").open("x") as log:
            process = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
            try:
                process.wait(timeout=1500)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    else:
        if read_json(claim)["config_sha256"] != digest(cfg):
            raise ValueError("Claimed attempt config changed")
        # A restarted controller attaches to the existing worker, never reissues it.
        while running_worker(directory):
            time.sleep(5)
    if result_path.exists():
        return read_json(result_path)
    if (directory / "trajectory.json").exists():
        return process_attempt(directory, cfg)
    result = {
        "task_id": task,
        "teacher": teacher_name,
        "eligible": False,
        "status": "interrupted",
        "official_success": None,
        "start_kind": "original_initial",
        "recovery_suffix": False,
        "usage": interrupted_usage(directory, cfg["teacher"]),
    }
    write_json(result_path, result)
    return result


def run_task(output, task, deployment):
    path = output / "results" / (task + ".json")
    if path.exists():
        return read_json(path)
    results = []
    for name in configuration(output_revision(output))["teacher_order"]:
        if (
            time.time() + 1500 > deployment["deadline_epoch"]
            or (output / "STOP").exists()
        ):
            return None
        result = attempt(output, task, name, deployment)
        results.append(result)
        if result["eligible"]:
            break
    value = {
        "task_id": task,
        "attempts": results,
        "eligible": any(r["eligible"] for r in results),
    }
    write_json(path, value)
    return value


def summary(output, status):
    results = [
        read_json(p) for p in sorted((output / "attempts").glob("*/result.json"))
    ]
    for source in (
        read_json(output / "campaign.json").get("carried_attempts", {}).values()
    ):
        if sha256(source["path"]) != source["sha256"]:
            raise ValueError("Carried Teacher result changed")
        results.append(read_json(source["path"]))
    campaign = read_json(output / "campaign.json")
    cfg = configuration(campaign["revision"])
    value = {
        "status": status,
        "updated_epoch": time.time(),
        "planned_tasks": len(campaign["task_ids"]),
        "finished_tasks": len(list((output / "results").glob("*.json"))),
        "attempts": len(results),
        "existing_eligible_recovery_suffixes": cfg[
            "existing_eligible_recovery_suffixes"
        ],
        "new_eligible_full_demonstrations": sum(r["eligible"] for r in results),
        "new_recovery_suffixes": 0,
        "training_started": False,
        "by_teacher": {
            name: {
                "attempts": sum(r["teacher"] == name for r in results),
                "official_successes": sum(
                    r["teacher"] == name and r.get("official_success") is True
                    for r in results
                ),
                "eligible": sum(
                    r["teacher"] == name and r["eligible"] for r in results
                ),
            }
            for name in cfg["teacher_order"]
        },
        "deferred": sum(r["status"] == "deferred" for r in results),
        "usage": {
            k: sum(r.get("usage", {}).get(k, 0) for r in results)
            for k in [
                "provider_requests",
                "prompt_tokens",
                "generated_tokens",
                "cached_prompt_tokens",
                "usage_priced_cost",
                "unknown_cost_reservation",
                "wall_seconds",
            ]
        },
    }
    temp = output / "progress.tmp"
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(output / "progress.json")
    return value


def collect(output, limit_tasks=None):
    revision = output_revision(output)
    lock_sha = verify_lock(revision)
    campaign, deployment = (
        read_json(output / "campaign.json"),
        read_json(output / "deployment.json"),
    )
    if campaign["source_lock_sha256"] != lock_sha or read_json(
        output / "config.json"
    ) != configuration(revision):
        raise ValueError("Campaign source/config changed")
    with (output / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        selected = (
            campaign["task_ids"][:limit_tasks] if limit_tasks else campaign["task_ids"]
        )
        with ThreadPoolExecutor(
            max_workers=configuration(revision)["concurrency"]
        ) as pool:
            futures = [
                pool.submit(run_task, output, task, deployment) for task in selected
            ]
            for future in as_completed(futures):
                future.result()
                print(json.dumps(summary(output, "running")), flush=True)
        finished = len(list((output / "results").glob("*.json")))
        value = summary(
            output, "complete" if finished == len(campaign["task_ids"]) else "partial"
        )
        if (
            value["status"] == "complete"
            and not (output / "export/manifest.json").exists()
        ):
            export(output)
        return value


def export(output):
    directory = output / "export"
    directory.mkdir(exist_ok=False)
    sources, samples = [], []
    for path in sorted((output / "results").glob("*.json")):
        for entry in read_json(path)["attempts"]:
            if entry["eligible"]:
                encoded = Path(entry["encoded_directory"])
                if (
                    sha256(encoded / "sample.json") != entry["sample_sha256"]
                    or sha256(encoded / "examples.jsonl.gz") != entry["examples_sha256"]
                ):
                    raise ValueError("Encoded follow-up demonstration changed")
                samples.append(read_json(encoded / "sample.json"))
                sources.append(entry)
    with (directory / "successes.jsonl").open("x") as stream:
        for sample in samples:
            stream.write(json.dumps(sample, ensure_ascii=False) + "\n")
    write_json(
        directory / "manifest.json",
        {
            "revision": output_revision(output),
            "kind": "full_teacher_demonstrations",
            "count": len(samples),
            "new_recovery_suffixes": 0,
            "sources": sources,
            "files": files_manifest(directory),
            "source_lock_sha256": sha256(paths(output_revision(output))[1]),
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "collect", "worker"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deployment", type=Path)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--limit-tasks", type=int)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.output, read_json(args.deployment))
    elif args.command == "worker":
        worker(args.output, args.credentials)
    else:
        collect(args.output, args.limit_tasks)


if __name__ == "__main__":
    main()
