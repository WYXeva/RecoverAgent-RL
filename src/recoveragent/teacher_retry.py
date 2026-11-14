"""One new attempt per unresolved train task, with a shared service pause."""

import argparse
import fcntl
import json
from pathlib import Path
import time

from . import teacher_escalation as escalation, teacher_followup as followup
from .demonstrations import sha256
from .harness import provider_error_record
from .io import read_json, write_json
from .teacher import TeacherPolicy, TeacherTransportError, read_credentials, usage_cost

REVISION = followup.RETRY


def configuration():
    followup.verify_lock(REVISION)
    return followup.configuration(REVISION)


def prepare(output, deployment):
    cfg = configuration()
    old = followup.configuration(followup.LOCAL_ESCALATION)
    previous = Path(deployment["previous_output"])
    pro = Path(deployment["pro_output"])
    rows = escalation.prior_results(previous, old)
    campaign = read_json(pro / "campaign.json")
    if (
        sha256(pro / "config.json") != cfg["retry_source"]["config_sha256"]
        or campaign["revision"] != followup.LOCAL_ESCALATION
        or campaign["source_lock_sha256"] != cfg["retry_source"]["source_lock_sha256"]
        or read_json(pro / "progress.json")["status"] != "complete"
        or campaign["task_ids"] != [r["task_id"] for _, r in rows if not r["eligible"]]
    ):
        raise ValueError("Previous Pro campaign is incomplete or changed")
    source_files = [p for p, _ in rows if p is not None]
    remaining = []
    for task in campaign["task_ids"]:
        path = pro / "results" / (task + ".json")
        result = read_json(path)
        attempts = result["attempts"]
        if (
            result["task_id"] != task
            or len(attempts) != 1
            or attempts[0]["task_id"] != task
            or attempts[0]["teacher"] != "deepseek_pro"
            or result["eligible"] != attempts[0]["eligible"]
        ):
            raise ValueError("Previous Pro result identity changed")
        source_files.append(path)
        if not result["eligible"]:
            remaining.append(task)
    if remaining != cfg["task_ids"]:
        raise ValueError("Retry selection differs from the frozen unresolved tasks")
    for root in (previous, pro):
        source_files.extend(
            root / n for n in ("campaign.json", "config.json", "progress.json")
        )
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config.json", cfg)
    write_json(output / "deployment.json", deployment)
    write_json(
        output / "campaign.json",
        {
            "revision": REVISION,
            "source_lock_sha256": followup.verify_lock(REVISION),
            "task_ids": remaining,
            "source_results": {str(p.resolve()): sha256(p) for p in source_files},
            "carried_attempts": {},
            "start_kind": "original_initial",
            "new_data_kind": "full_teacher_demonstration_not_recovery_suffix",
            "authorization": "User requested recollection of the 27 tasks without qualified data; one additional attempt each, original attempts preserved.",
        },
    )
    if deployment.get("not_before_epoch", 0) > time.time():
        pause(output, deployment["not_before_epoch"], "provider_retry_after")


def pause(output, retry_at, reason):
    receipt = {
        "recorded_epoch": time.time(),
        "retry_at_epoch": retry_at,
        "reason": reason,
    }
    write_json(output / "service_pauses" / (str(time.time_ns()) + ".json"), receipt)
    temp = output / "service_pause.tmp"
    temp.write_text(json.dumps(receipt) + "\n")
    temp.replace(output / "service_pause.json")


def pause_delay(error, cfg):
    if error.get("http_status") != 429:
        return None
    delay = error.get("diagnostic", {}).get("retry_after_seconds")
    if type(delay) is not int or delay < 0:
        delay = cfg["service_pause"]["fallback_seconds"]
    return delay + cfg["service_pause"]["margin_seconds"]


def availability(output, deployment, cfg):
    """A small recorded request confirms quota before claiming a task."""
    probe = output / "service_probes" / str(time.time_ns())
    worker = followup.worker_config(
        cfg["task_ids"][0],
        output.name,
        deployment["appworld_root"],
        "deepseek_pro",
        revision=REVISION,
    )
    policy = TeacherPolicy(worker, read_credentials(deployment["credentials"]))
    payload = {
        "model": worker["teacher"]["model"],
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "thinking": {"type": "disabled"},
        "max_tokens": 64,
        "stream": False,
    }
    write_json(probe / "request.json", payload)
    try:
        response = policy.transport(payload)
    except TeacherTransportError as exc:
        error = provider_error_record(exc)
        error["diagnostic"] = exc.diagnostic or {}
        write_json(
            probe / "result.json",
            {"ready": False, "provider_error": error, "usage_known": False},
        )
        return error
    write_json(probe / "response.json", response)
    from .p3a import verified_response_usage

    usage, _ = verified_response_usage(response, worker["teacher"], 64)
    write_json(
        probe / "result.json",
        {
            "ready": True,
            "usage": usage,
            "usage_priced_cost": usage_cost(usage, worker["teacher"]["pricing"]),
            "purpose": "service_availability_not_task_attempt",
        },
    )
    return None


def collect(output):
    cfg = configuration()
    deployment = read_json(output / "deployment.json")
    campaign = read_json(output / "campaign.json")
    escalation.verify_source_results(output, deployment.get("artifact_path_map", {}))
    if read_json(output / "config.json") != cfg or campaign[
        "source_lock_sha256"
    ] != followup.verify_lock(REVISION):
        raise ValueError("Retry source/configuration changed")
    with (output / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if all(
            (output / "results" / (t + ".json")).exists() for t in campaign["task_ids"]
        ):
            value = followup.summary(output, "complete")
            if not (output / "export/manifest.json").exists():
                followup.export(output)
            return value
        if (output / "STOP").exists() or time.time() + 1500 > deployment[
            "deadline_epoch"
        ]:
            return followup.summary(output, "partial")
        gate = output / "service_pause.json"
        if gate.exists() and read_json(gate)["retry_at_epoch"] > time.time():
            return followup.summary(output, "waiting_for_service")
        error = availability(output, deployment, cfg)
        if error:
            delay = pause_delay(error, cfg)
            if delay is None:
                write_json(
                    output / "service_blocked" / (str(time.time_ns()) + ".json"), error
                )
                return followup.summary(output, "service_blocked")
            pause(output, time.time() + delay, "availability_http_429")
            return followup.summary(output, "waiting_for_service")
        # Sequential dispatch: a quota refusal cannot consume the remaining queue.
        for task in campaign["task_ids"]:
            result_path = output / "results" / (task + ".json")
            if result_path.exists():
                continue
            result = followup.run_task(output, task, deployment)
            if result is None:
                return followup.summary(output, "partial")
            for attempt in result["attempts"]:
                path = (
                    output
                    / "attempts"
                    / f"{output.name}_{task}_{attempt['teacher']}"
                    / "trajectory.json"
                )
                error = (
                    read_json(path).get("provider_error", {}) if path.exists() else {}
                )
                delay = pause_delay(error, cfg)
                if delay is not None:
                    pause(output, time.time() + delay, "task_http_429")
                    return followup.summary(output, "waiting_for_service")
            followup.summary(output, "running")
        value = followup.summary(output, "complete")
        if not (output / "export/manifest.json").exists():
            followup.export(output)
        return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "collect"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deployment", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.stage == "prepare":
        prepare(output, read_json(args.deployment))
    else:
        print(collect(output), flush=True)
