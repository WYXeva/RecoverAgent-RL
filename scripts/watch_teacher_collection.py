"""Watch one frozen Teacher campaign; preserve failed attempts while continuing.

No model-generation probes, task retries, process kills, or campaign edits. Durable
watchdog evidence stays under the private run directory. Linux /proc and flock are
required; the collector retains its own exclusive lock across all paid work.
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from recoveragent import teacher_collection as collection
from recoveragent.teacher import read_credentials

TRANSPORT_ERRORS = {
    "ProxyError",
    "SSLError",
    "ConnectionError",
    "ConnectTimeout",
    "ReadTimeout",
    "Timeout",
    "TimeoutError",
    "ConnectionResetError",
    "ConnectionAbortedError",
}
TERMINAL = {"target_reached", "pool_exhausted", "cost_cap", "stopped_by_control_file"}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    destination = path if exclusive else path.with_suffix(path.suffix + ".tmp")
    with destination.open("x" if exclusive else "w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if not exclusive:
        destination.replace(path)


def process(pid, output):
    """Match actual Python argv/run paths, not a reused PID or shell substring."""
    try:
        proc = Path("/proc") / str(pid)
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        argv = (proc / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
        if fields[0] == "Z" or not Path(argv[0]).name.startswith("python"):
            return None
        cwd = (proc / "cwd").resolve()
        destination = (cwd / argv[argv.index("--output") + 1]).resolve()
        script = collection.ROOT / "scripts/collect_teacher_64k.py"
        if (
            len(argv) > 2
            and (cwd / argv[1]).resolve() == script
            and argv[2] == "collect"
        ):
            kind = "collector" if destination == output else None
        elif (
            argv[1:3] == ["-m", "recoveragent.teacher_collection"]
            and argv[3] == "worker"
        ):
            kind = "worker" if destination.parent == output / "attempts" else None
        elif argv[1:3] == ["-m", "recoveragent.evaluation"]:
            kind = (
                "evaluation"
                if destination.is_relative_to(output / "attempts")
                else None
            )
        else:
            return None
        if not kind:
            return None
        start_ticks = int(fields[19])
        return {
            "pid": int(pid),
            "kind": kind,
            "start_ticks": start_ticks,
            "elapsed_seconds": round(
                float(Path("/proc/uptime").read_text().split()[0])
                - start_ticks / os.sysconf("SC_CLK_TCK"),
                1,
            ),
        }
    except (OSError, ValueError, IndexError):
        return None


def processes(output):
    return [
        row
        for path in Path("/proc").iterdir()
        if path.name.isdigit()
        if (row := process(path.name, output))
    ]


def transient(result):
    error = result.get("provider_error") or {}
    diagnostic = error.get("diagnostic") or {}
    usage = result.get("usage") or {}
    reserved = [
        usage.get("unknown_generation_reservation"),
        usage.get("unknown_cost_reservation"),
    ]
    return (
        result.get("status") == "infrastructure_error"
        and result.get("termination") == "infrastructure_error"
        and result.get("official_success") is None
        and result.get("eligible") is False
        and error.get("kind") == "transport_error"
        and "http_status" in error
        and error["http_status"] is None
        and error.get("usage_known") is False
        and diagnostic.get("response_status") is None
        and diagnostic.get("transport_error_type") in TRANSPORT_ERRORS
        and all(
            type(value) in (int, float) and math.isfinite(value) and value > 0
            for value in reserved
        )
    )


def task_evaluation_quarantine(output, result):
    """Recognize a complete negative task report without changing its unknown score.

    All evidence stays on the controller. Exception names and hidden assertion
    text do not determine admission; report integrity and environment health do.
    """
    from recoveragent.evaluation import classify_official

    if (
        result.get("status") != "infrastructure_error"
        or result.get("termination") != "evaluation_error"
        or result.get("official_success") is not None
        or result.get("eligible") is not False
        or result.get("provider_error")
        or result.get("evaluation_sha256") is not None
    ):
        return None
    task = result.get("task_id")
    if not isinstance(task, str) or Path(task).name != task:
        return None
    directory = output / "attempts" / (output.name + "_" + task)
    paths = {
        "result_sha256": output / "results" / (task + ".json"),
        "attempt_result_sha256": directory / "result.json",
        "trajectory_sha256": directory / "trajectory.json",
        "evaluation_sha256": directory / "isolated_evaluation.json",
        "worker_config_sha256": directory.with_suffix(".config.json"),
        "evaluation_research_sha256": directory / "evaluation_research.json",
    }
    if not all(path.is_file() for path in paths.values()):
        return None
    record = read(paths["trajectory_sha256"])
    evaluated = read(paths["evaluation_sha256"])
    config = read(paths["worker_config_sha256"])
    context = config.get("custom_research", {})
    if (
        task not in read(output / "campaign.json")["tasks"]
        or read(paths["result_sha256"]) != result
        or read(paths["attempt_result_sha256"])
        != {key: value for key, value in result.items() if key != "worker_wall_seconds"}
        or sha(paths["trajectory_sha256"]) != result.get("trajectory_sha256")
        or config != collection.worker_config(task, output.name)
        or config.get("collection_run_id") != output.name
        or context.get("task_id") != task
        or context.get("role") != "train"
        or record.get("custom_research") != context
        or read(paths["evaluation_research_sha256"]) != context
        or record.get("task_id") != task
        or record.get("trajectory_id") != directory.name
        or record.get("status") != "infrastructure_error"
        or record.get("reward") is not None
        or record.get("training_started") is not False
        or record.get("termination_reason") != "evaluation_error"
        or record.get("failure_category") != "evaluation_runtime"
        or record.get("usage") != result.get("usage")
        or record.get("provider_error")
        or record.get("environment_error")
        or record.get("cleanup_error")
        or record.get("environment_check", {}).get("uri_roundtrip") is not True
        or evaluated.get("task_id") != task
        or evaluated.get("experiment") != directory.name
        or evaluated.get("environment", {}).get("uri_roundtrip") is not True
        or evaluated.get("environment_error")
        or evaluated.get("provider_error")
        or (directory / "evaluation.json").exists()
    ):
        return None
    official, observations = (
        evaluated.get("official", {}),
        evaluated.get("observations"),
    )
    if (
        not isinstance(observations, list)
        or not observations
        or any(
            not isinstance(item, dict)
            or item.get("category")
            not in {"task_assertion", "task_state_failure", "unknown_exception"}
            or not isinstance(item.get("exception_type"), str)
            or not item["exception_type"]
            for item in observations
        )
        or not isinstance(official.get("failures"), list)
        or any(
            not isinstance(item, dict)
            or any(
                not isinstance(item.get(key), str) or not item[key]
                for key in ("requirement", "trace")
            )
            for item in official["failures"]
        )
    ):
        return None
    classified = classify_official(official, observations)
    if (
        classified["status"] != "evaluation_error"
        or classified["category"] != "unknown"
        or classified["success"] is not None
        or classified["official_success"] is not False
        or classified["official_result_complete"] is not True
        or any(evaluated.get(key) != value for key, value in classified.items())
        or record.get("evaluation_error") != classified["exception_types"]
    ):
        return None
    return {
        "protocol": "complete_negative_task_evaluation_quarantine_v1",
        "task_id": task,
        "experiment": directory.name,
        **{key: sha(path) for key, path in paths.items()},
        "num_tests": official["num_tests"],
        "passes": len(official["passes"]),
        "failures": len(official["failures"]),
        "action": "exclude_unknown_task_and_continue_next_unattempted",
        "retained_official_success": None,
        "eligible": False,
        "training_started": False,
        "retry_failed_task": False,
        "actor_receives_evaluation": False,
    }


def probe_models(cfg, credentials_path):
    """Authenticated GET only; never retain credentials, response bodies or URLs."""
    import requests

    credentials = read_credentials(credentials_path)
    teacher = cfg["teacher"]
    if (
        credentials.get("DEEPSEEK_BASE_URL", "").rstrip("/") != teacher["base_url"]
        or credentials.get("DEEPSEEK_MODEL") != teacher["model"]
        or not credentials.get(teacher["api_key_env"])
    ):
        return {"outcome": "blocked", "reason": "credential_identity_mismatch"}
    try:
        headers = {"Authorization": "Bearer " + credentials[teacher["api_key_env"]]}
        if teacher.get("http_user_agent"):
            headers["User-Agent"] = teacher["http_user_agent"]
        with requests.get(
            teacher["base_url"] + "/models",
            headers=headers,
            timeout=(10, 15),
            allow_redirects=False,
        ) as response:
            status = response.status_code
            if 500 <= status < 600:
                return {
                    "outcome": "waiting",
                    "reason": "models_service_unavailable",
                    "http_status": status,
                }
            if status != 200:
                # HTTP errors (including quota/auth/rate limits) require review.
                return {
                    "outcome": "blocked",
                    "reason": "models_http_error",
                    "http_status": status,
                }
            present = any(
                row.get("id") == teacher["model"]
                for row in response.json().get("data", [])
            )
            return {
                "outcome": "ready" if present else "blocked",
                "http_status": 200,
                "teacher_present": present,
                "reason": "models_checked",
            }
    except requests.exceptions.InvalidURL:
        return {"outcome": "blocked", "reason": "models_configuration_error"}
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        return {
            "outcome": "waiting",
            "reason": "models_transport_error",
            "exception_type": type(exc).__name__,
        }
    except (ValueError, AttributeError, TypeError):
        return {"outcome": "blocked", "reason": "models_invalid_response"}


class Watchdog:
    def __init__(self, output, expected_lock, interval=30, probe_interval=60):
        self.output = output.resolve()
        self.directory = self.output / "watchdog"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.lock = (self.directory / "watchdog.lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.expected_lock = expected_lock
        self.cfg = collection.configuration()
        self.verify_source()
        self.config = {
            "output": str(self.output),
            "source_lock_sha256": expected_lock,
            "script_sha256": sha(Path(__file__)),
            "campaign_sha256": sha(self.output / "campaign.json"),
            "config_sha256": sha(self.output / "config.json"),
            "interval_seconds": interval,
            "probe_interval_seconds": probe_interval,
            "python": sys.executable,
            "no_generation_probe": True,
        }
        config_path = self.directory / "config.json"
        if config_path.exists():
            if read(config_path) != self.config:
                raise ValueError("Watchdog configuration changed")
        else:
            save(config_path, self.config, exclusive=True)
            # Keep the exact monitor implementation with its private evidence.
            (self.directory / "implementation.py").write_bytes(
                Path(__file__).read_bytes()
            )
        state_path = self.directory / "state.json"
        self.state = (
            read(state_path)
            if state_path.exists()
            else {
                "handled_faults": {},
                "next_probe_epoch": 0,
            }
        )
        self.child = None
        self.import_reviews()
        self.persist()
        self.event(
            "watchdog_started",
            pid=os.getpid(),
            script_sha256=self.config["script_sha256"],
        )

    def verify_source(self):
        if collection.verify_lock() != self.expected_lock:
            raise ValueError("Unexpected collection source lock")
        if (
            read(self.output / "config.json") != collection.configuration()
            or read(self.output / "campaign.json")["source_lock_sha256"]
            != self.expected_lock
        ):
            raise ValueError("Campaign configuration changed")
        if hasattr(self, "config"):
            for name in ("campaign", "config"):
                if sha(self.output / (name + ".json")) != self.config[name + "_sha256"]:
                    raise ValueError("Campaign input changed")
            if sha(Path(__file__)) != self.config["script_sha256"]:
                raise ValueError("Watchdog implementation changed")

    def import_reviews(self):
        for path in sorted((self.output / "resume_reviews").glob("*.json")):
            review = read(path)
            task = review.get("exception_task")
            if not task or Path(task).name != task:
                continue
            result_path = self.output / "results" / (task + ".json")
            if (
                review.get("source_lock_sha256") == self.expected_lock
                and review.get("action")
                == "resume_same_campaign_on_next_unattempted_task"
                and result_path.exists()
            ):
                result = read(result_path)
                if transient(result) and review.get("trajectory_sha256") == result.get(
                    "trajectory_sha256"
                ):
                    self.state["handled_faults"].setdefault(
                        sha(result_path),
                        {
                            "task_id": task,
                            "manual_review_sha256": sha(path),
                        },
                    )
                elif result.get("termination") == "evaluation_error":
                    evidence = task_evaluation_quarantine(self.output, result)
                    if (
                        evidence
                        and review.get("result_sha256")
                        in {
                            evidence["result_sha256"],
                            evidence["attempt_result_sha256"],
                        }
                        and all(
                            review.get(key) == evidence[key]
                            for key in (
                                "trajectory_sha256",
                                "evaluation_sha256",
                            )
                        )
                    ):
                        self.record_quarantine(evidence)
                        self.state["handled_faults"].setdefault(
                            evidence["result_sha256"],
                            {
                                "task_id": task,
                                "manual_review_sha256": sha(path),
                                "quarantine": self.state["evaluation_quarantines"][
                                    evidence["result_sha256"]
                                ],
                            },
                        )

    def persist(self):
        save(self.directory / "state.json", self.state)

    def event(self, name, **details):
        with (self.directory / "events.jsonl").open("a") as stream:
            stream.write(
                json.dumps({"epoch": time.time(), "event": name, **details}) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    def report(self, status, **details):
        value = {
            "updated_epoch": time.time(),
            "pid": os.getpid(),
            "status": status,
            **details,
        }
        save(self.directory / "heartbeat.json", value)
        if self.state.get("last_status") != status:
            self.event("status_changed", **value)
            self.state["last_status"] = status
            self.persist()
        return value

    def block(self, reason, **details):
        self.state["blocked"] = {"reason": reason, **details}
        self.persist()
        return self.report("blocked", **self.state["blocked"])

    def inventory(self):
        results = {}
        for path in sorted((self.output / "results").glob("*.json")):
            row = read(path)
            if row["task_id"] != path.stem or row["task_id"] in results:
                raise ValueError("Invalid result identity")
            results[row["task_id"]] = {"row": row, "sha256": sha(path)}
        claims = set()
        for path in (self.output / "claims").glob("*.json"):
            if read(path).get("task_id") != path.stem:
                raise ValueError("Invalid claim identity")
            claims.add(path.stem)
        return results, claims

    def tick(self):
        if self.child is not None:
            self.child.poll()  # Reap a finished resumed collector, without sending signals.
        progress = read(self.output / "progress.json")
        live = processes(self.output)
        collectors = [row for row in live if row["kind"] == "collector"]
        workers = [row for row in live if row["kind"] != "collector"]
        details = {
            "collector_processes": collectors,
            "worker_processes": workers,
            "total_eligible": progress.get("total_eligible"),
            "target_successes": self.cfg["target_successes"],
            "progress_status": progress["status"],
            "progress_age_seconds": round(time.time() - progress["updated_epoch"], 1),
        }
        if (self.output / "STOP").exists() or (self.directory / "STOP").exists():
            self.state["terminal"] = "stopped_by_control_file"
            self.persist()
        if self.state.get("terminal"):
            return self.report(self.state["terminal"], **details)
        if collectors:
            self.state.pop("absent_since", None)
            if len(collectors) != 1:
                return self.block("multiple_collectors", **details)
            stale = details["progress_age_seconds"] > 90 or any(
                row["elapsed_seconds"] > 1650 for row in workers
            )
            return self.report(
                "attention_stale_process" if stale else "monitoring", **details
            )
        if workers:
            self.state.pop("absent_since", None)
            self.persist()
            return self.report("waiting_for_orphan_workers", **details)
        if progress["status"] in TERMINAL:
            return self.finish(progress["status"], details)
        absent_since = self.state.setdefault("absent_since", time.time())
        self.persist()
        if time.time() - absent_since < self.config["interval_seconds"]:
            return self.report("confirming_collector_exit", **details)
        self.verify_source()
        results, claims = self.inventory()
        if claims - results.keys():
            self.resolve_interrupted(claims - results.keys())
            results, claims = self.inventory()
            if claims - results.keys():
                return self.report("waiting_for_claim_resolution", **details)
        if results.keys() - claims:
            return self.block("results_without_claims", **details)
        faults = {
            task: item
            for task, item in results.items()
            if collection.blocks_campaign(item["row"])
            and item["sha256"] not in self.state["handled_faults"]
        }
        if not all(self.recoverable(item["row"]) for item in faults.values()):
            return self.block("unreviewed_nontransport_or_missing_fault", **details)
        campaign = read(self.output / "campaign.json")
        count = campaign["reused_eligible"] + sum(
            item["row"]["eligible"] for item in results.values()
        )
        spent = sum(
            row.get("usage", {}).get("usage_priced_cost", 0)
            + row.get("usage", {}).get("unknown_cost_reservation", 0)
            for row in campaign.get("prior_attempts", [])
            + [item["row"] for item in results.values()]
        )
        if count >= self.cfg["target_successes"]:
            reason = "target_reached"
        elif not set(campaign["tasks"]) - claims:
            reason = "pool_exhausted"
        elif spent + self.cfg["episode_cost_cap_usd"] > self.cfg["total_cost_cap_usd"]:
            reason = "cost_cap"
        else:
            reason = None
        if reason:
            return self.finish(reason, details)
        if self.state.get("provider_hold"):
            hold = self.state["provider_hold"]
            credential_path = collection.ROOT / ".env"
            if (
                hold["probe"].get("http_status") in {401, 403}
                and credential_path.exists()
                and sha(credential_path) != self.state.get("held_credentials_sha256")
            ):
                self.state.pop("provider_hold")
                self.persist()
            else:
                return self.report("blocked", **hold, **details)
        if time.time() < self.state["next_probe_epoch"]:
            return self.report("waiting_for_network", **details)
        self.state["next_probe_epoch"] = (
            time.time() + self.config["probe_interval_seconds"]
        )
        self.persist()
        probe = probe_models(self.cfg, collection.ROOT / ".env")
        self.event("models_probe", **probe)
        if probe["outcome"] == "blocked":
            self.state["provider_hold"] = {"reason": probe["reason"], "probe": probe}
            credential_path = collection.ROOT / ".env"
            self.state["held_credentials_sha256"] = (
                sha(credential_path) if credential_path.exists() else None
            )
            return self.block(probe["reason"], probe=probe, **details)
        if probe["outcome"] != "ready":
            return self.report("waiting_for_network", probe=probe, **details)
        return self.resume(faults, claims, probe)

    def recoverable(self, row):
        if transient(row):
            return True
        evidence = task_evaluation_quarantine(self.output, row)
        if evidence:
            self.record_quarantine(evidence)
            return True
        if (row.get("provider_error") or {}).get("http_status") in {401, 403}:
            path = collection.ROOT / ".env"
            current = sha(path) if path.exists() else None
            previous = self.state.setdefault("auth_fault_credentials", {}).setdefault(
                row["task_id"], current
            )
            self.persist()
            return previous != current and current is not None
        name = row.get("watchdog_interrupt_audit")
        if not name or Path(name).name != name:
            return False
        path = self.directory / "interrupt_reviews" / name
        if sha(path) != row.get("watchdog_interrupt_audit_sha256"):
            raise ValueError("Interrupt audit changed")
        audit = read(path)
        return (
            row.get("status") == "worker_error"
            and row.get("eligible") is False
            and row.get("official_success") is None
            and audit["task_id"] == row["task_id"]
            and audit["source_lock_sha256"] == self.expected_lock
            and audit["usage"] == row["usage"]
        )

    def record_quarantine(self, evidence):
        review = {
            **evidence,
            "source_lock_sha256": self.expected_lock,
            "watchdog_config_sha256": sha(self.directory / "config.json"),
        }
        name = (
            evidence["result_sha256"]
            + "_"
            + self.config["script_sha256"][:12]
            + ".json"
        )
        path = self.directory / "quarantine_reviews" / name
        if path.exists():
            if any(read(path).get(key) != value for key, value in review.items()):
                raise ValueError("Task evaluation quarantine evidence changed")
        else:
            save(path, {**review, "created_epoch": time.time()}, exclusive=True)
            self.event(
                "task_evaluation_quarantined",
                task_id=evidence["task_id"],
                audit_sha256=sha(path),
            )
        self.state.setdefault("evaluation_quarantines", {})[
            evidence["result_sha256"]
        ] = {
            "review": name,
            "sha256": sha(path),
            "evidence": evidence,
        }
        self.persist()

    def finish(self, reason, details):
        """Ensure a final complete pool exists even if the collector died before export."""
        with (self.output / "collector.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self.report("waiting_for_final_export", **details)
            if processes(self.output):
                return self.report("waiting_for_final_export", **details)
            self.verify_source()
            campaign = read(self.output / "campaign.json")
            results, claims = self.inventory()
            if claims != results.keys():
                return self.block("unresolved_claims_before_final_export", **details)
            expected = {
                row["task_id"] for row in campaign.get("reuse", []) if row["eligible"]
            }
            expected |= {
                task for task, item in results.items() if item["row"]["eligible"]
            }
            for manifest_path in (self.output / "exports").glob("*/manifest.json"):
                manifest = read(manifest_path)
                if (
                    manifest.get("count") == len(expected)
                    and {row["task_id"] for row in manifest.get("sources", [])}
                    == expected
                    and all(
                        sha(manifest_path.parent / name) == value
                        for name, value in manifest["files"].items()
                    )
                ):
                    break
            else:
                destination = collection.export(self.output)
                self.event(
                    "final_pool_exported",
                    directory=str(destination),
                    count=len(expected),
                )
            self.state["terminal"] = reason
            self.persist()
            return self.report(reason, **details)

    def resolve_interrupted(self, unresolved):
        """Seal a dead attempt without re-running it or altering durable evidence."""
        from recoveragent.p3a import interrupted_usage, verified_response_usage

        with (self.output / "collector.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            self.verify_source()
            if processes(self.output) or (self.output / "STOP").exists():
                return
            campaign = read(self.output / "campaign.json")
            for task in sorted(unresolved):
                if task not in campaign["tasks"]:
                    raise ValueError("Claim outside frozen task list")
                claim = self.output / "claims" / (task + ".json")
                if read(claim).get("task_id") != task:
                    raise ValueError("Invalid claim identity")
                target = self.output / "results" / (task + ".json")
                if target.exists():
                    continue
                directory = self.output / "attempts" / (self.output.name + "_" + task)
                worker_config = directory.with_suffix(".config.json")
                if worker_config.exists():
                    if read(worker_config) != collection.worker_config(
                        task, self.output.name
                    ):
                        raise ValueError("Interrupted worker configuration changed")
                elif directory.exists():
                    raise ValueError("Interrupted worker configuration missing")
                evidence = {str(claim.relative_to(self.output)): sha(claim)}
                # Parse every present JSON first: partial records require review,
                # rather than interrupted_usage's permissive missing-response path.
                for path in sorted(directory.rglob("*.json")):
                    read(path)
                    evidence[str(path.relative_to(self.output))] = sha(path)
                original = directory / "result.json"
                if original.exists():
                    result = read(original)
                    if (
                        result.get("task_id") != task
                        or type(result.get("eligible")) is not bool
                        or result.get("status")
                        not in {
                            "eligible",
                            "deferred",
                            "filtered",
                            "failed",
                            "infrastructure_error",
                        }
                        or sha(directory / "trajectory.json")
                        != result.get("trajectory_sha256")
                    ):
                        raise ValueError("Invalid completed attempt result")
                    if (
                        result.get("evaluation_sha256")
                        and sha(directory / "evaluation.json")
                        != result["evaluation_sha256"]
                    ):
                        raise ValueError("Completed evaluation changed")
                    if result["eligible"]:
                        for name, field in (
                            ("sample.json", "sample_sha256"),
                            ("examples.jsonl.gz", "examples_sha256"),
                        ):
                            if sha(directory / "encoded" / name) != result[field]:
                                raise ValueError("Completed encoding changed")
                    action = "copy_completed_attempt_result"
                else:
                    trajectory = directory / "trajectory.json"
                    if trajectory.exists():
                        record = read(trajectory)
                        if record.get("provider_error"):
                            candidate = {
                                "status": "infrastructure_error",
                                "eligible": False,
                                "official_success": None,
                                "termination": record.get("termination_reason"),
                                "provider_error": record["provider_error"],
                                "usage": record.get("usage", {}),
                            }
                            if not transient(candidate):
                                raise ValueError(
                                    "Completed provider error requires review"
                                )
                        elif record.get("termination_reason") == "evaluation_error":
                            raise ValueError(
                                "Completed evaluation error requires review"
                            )
                        elif record.get(
                            "status"
                        ) == "infrastructure_error" or record.get("error_type"):
                            raise ValueError(
                                "Completed execution error requires review"
                            )
                    backend = None
                    for reservation_path in sorted(
                        (directory / "requests").glob("*.reservation.json")
                    ):
                        reservation = read(reservation_path)
                        response = reservation_path.with_name(
                            reservation_path.name.replace(".reservation.", ".response.")
                        )
                        if not (
                            0
                            < reservation["max_tokens"]
                            <= self.cfg["teacher"]["max_new_tokens"]
                            and 0
                            < reservation["max_cost"]
                            <= self.cfg["episode_cost_cap_usd"]
                        ):
                            raise ValueError("Invalid interrupted reservation")
                        if response.exists():
                            _, backend = verified_response_usage(
                                read(response),
                                self.cfg["teacher"],
                                reservation["max_tokens"],
                                backend,
                            )
                    result = {
                        "task_id": task,
                        "status": "worker_error",
                        "eligible": False,
                        "official_success": None,
                        "termination": "watchdog_confirmed_process_interruption",
                        "usage": interrupted_usage(directory, self.cfg["teacher"]),
                        "trajectory_sha256": sha(directory / "trajectory.json")
                        if (directory / "trajectory.json").exists()
                        else None,
                    }
                    action = "seal_interrupted_attempt_unknown_without_retry"
                stamp = str(time.time_ns()) + ".json"
                audit_path = self.directory / "interrupt_reviews" / stamp
                save(
                    audit_path,
                    {
                        "epoch": time.time(),
                        "task_id": task,
                        "action": action,
                        "source_lock_sha256": self.expected_lock,
                        "absent_since": self.state["absent_since"],
                        "evidence": evidence,
                        "usage": result["usage"],
                        "retry": False,
                    },
                    exclusive=True,
                )
                if original.exists():
                    target.parent.mkdir(exist_ok=True)
                    with target.open("xb") as stream:
                        stream.write(original.read_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
                else:
                    result.update(
                        watchdog_interrupt_audit=stamp,
                        watchdog_interrupt_audit_sha256=sha(audit_path),
                    )
                    save(target, result, exclusive=True)
                self.event(
                    "interrupted_claim_resolved",
                    task_id=task,
                    action=action,
                    result_sha256=sha(target),
                    audit_sha256=sha(audit_path),
                )

    def resume(self, faults, claims, probe):
        with (self.output / "collector.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self.report("collector_lock_busy")
            self.verify_source()
            if processes(self.output):
                return self.report("process_appeared_before_resume")
            if (self.output / "STOP").exists() or (self.directory / "STOP").exists():
                self.state["terminal"] = "stopped_by_control_file"
                self.persist()
                return self.report("stopped_by_control_file")
            now_results, now_claims = self.inventory()
            if now_claims != claims or now_claims != now_results.keys():
                return self.block("claims_changed_before_resume")
            for task, item in faults.items():
                trajectory = (
                    self.output
                    / "attempts"
                    / (self.output.name + "_" + task)
                    / "trajectory.json"
                )
                expected = item["row"].get("trajectory_sha256")
                observed = sha(trajectory) if trajectory.exists() else None
                if now_results[task] != item or observed != expected:
                    return self.block("fault_evidence_changed")
                quarantine = self.state.get("evaluation_quarantines", {}).get(
                    item["sha256"]
                )
                if quarantine and (
                    task_evaluation_quarantine(self.output, item["row"])
                    != quarantine["evidence"]
                    or sha(self.directory / "quarantine_reviews" / quarantine["review"])
                    != quarantine["sha256"]
                ):
                    return self.block("task_evaluation_quarantine_changed")
            stamp = str(time.time_ns())
            command = [
                sys.executable,
                str(collection.ROOT / "scripts/collect_teacher_64k.py"),
                "collect",
                "--output",
                str(self.output),
            ]
            audit = {
                "action": "resume_same_campaign_on_next_unattempted_task",
                "epoch": time.time(),
                "probe": probe,
                "resolved_claims": len(claims),
                "source_lock_sha256": self.expected_lock,
                "watchdog_config_sha256": sha(self.directory / "config.json"),
                "faults": {
                    task: {
                        "result_sha256": item["sha256"],
                        "usage": item["row"]["usage"],
                        "task_evaluation_quarantine": self.state.get(
                            "evaluation_quarantines", {}
                        ).get(item["sha256"]),
                    }
                    for task, item in faults.items()
                },
                "retry_failed_tasks": False,
                "command": command,
            }
            save(
                self.directory / "resume_reviews" / (stamp + ".json"),
                audit,
                exclusive=True,
            )
            # Consume before launch so a restarted monitor never retries these tasks.
            for task, item in faults.items():
                self.state["handled_faults"][item["sha256"]] = {
                    "task_id": task,
                    "resume_review": stamp,
                }
            self.persist()
        # The unmodified collector takes collector.lock before touching any claims;
        # an external collector winning this small gap cannot cause duplicate work.
        environment = dict(
            os.environ,
            HF_HOME=str(collection.ROOT / ".cache/v2-hf"),
            PYTHONPATH=str(collection.ROOT / "src"),
            TOKENIZERS_PARALLELISM="false",
            OMP_NUM_THREADS="2",
            OPENBLAS_NUM_THREADS="2",
            PYTHONFAULTHANDLER="1",
        )
        log_path = self.directory / ("collector_resume_" + stamp + ".log")
        with log_path.open("x") as log:
            self.child = subprocess.Popen(
                command,
                cwd=collection.ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.state["last_launch"] = {"pid": self.child.pid, "resume_review": stamp}
        self.persist()
        self.event("collector_resumed", **self.state["last_launch"])
        return self.report("resuming", collector_pid=self.child.pid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--lock-sha256", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    watcher = Watchdog(args.output, args.lock_sha256)
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        try:
            value = watcher.tick()
        except Exception as exc:
            value = watcher.block(
                "watchdog_check_failed", exception_type=type(exc).__name__
            )
        if value["status"] in TERMINAL:
            break
        # A signal can stop the monitor without touching any paid worker.
        for _ in range(watcher.config["interval_seconds"]):
            if stopping:
                break
            time.sleep(1)
    if stopping:
        watcher.report("watchdog_stopped")


if __name__ == "__main__":
    main()
