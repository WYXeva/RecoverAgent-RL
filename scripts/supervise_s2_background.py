"""Keep S2 Student and CPU/API Teacher independent of SSH until the fixed deadline."""

import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import time

from recoveragent.io import read_json
from recoveragent.s2_state import atomic
from recoveragent.s2_reporting import report


def retryable(error_type, message, seconds_left):
    if seconds_left < 1320:
        return False
    fatal = (
        "nonfinite",
        "non-finite",
        "reload",
        "hash",
        "identity",
        "changed",
        "configuration",
        "source lock",
    )
    if error_type in {"ValueError", "AssertionError"} or any(
        s in message.lower() for s in fatal
    ):
        return False
    return error_type in {
        "RuntimeError",
        "TimeoutError",
        "ConnectionError",
        "OSError",
        "CalledProcessError",
        "BrokenPipeError",
        "ProcessExit",
    }


def main():
    import psutil
    from recoveragent.s2_experiment import configuration
    from recoveragent.s2_backend import BackendRuntime
    from recoveragent.v2_runtime import s2_execution

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cfg = configuration()
    if not s2_execution().get("background_generation"):
        raise ValueError("Explicit background execution required")
    deployment = read_json(args.deployment)
    output = args.output.resolve()
    root = args.deployment.resolve().parent
    folder = output / "supervision"
    folder.mkdir(parents=True, exist_ok=True)
    lock = (folder / "supervisor.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    runtime = BackendRuntime(cfg, deployment, output / "gpu")
    prefix = [deployment["worker_python"], "-u", "-m", "recoveragent.s2_experiment"]
    common = ["--deployment", str(args.deployment.resolve()), "--output", str(output)]
    commands = {
        "student": prefix + ["resume", "--mode", "evolve"] + common,
        "teacher": prefix + ["feedback-worker"] + common,
    }
    processes, known, restarts, disabled = {}, {}, {"student": 0, "teacher": 0}, {}
    owned = {"student": {}, "teacher": {}}
    last_progress = None
    log_handles = []

    def event(kind, **details):
        import json

        with (folder / "events.jsonl").open("a") as stream:
            stream.write(
                json.dumps({"epoch": time.time(), "kind": kind, **details}) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    def remember(process, role=None):
        try:
            for child in [
                psutil.Process(process.pid),
                *psutil.Process(process.pid).children(recursive=True),
            ]:
                known[child.pid] = child.create_time()
                if role is not None:
                    owned[role][child.pid] = child.create_time()
        except psutil.NoSuchProcess:
            pass

    def stop_owned(identities=None):
        identities = dict(known if identities is None else identities)
        for pid, started in list(identities.items()):
            try:
                p = psutil.Process(pid)
                if p.create_time() == started:
                    identities.update(
                        {c.pid: c.create_time() for c in p.children(recursive=True)}
                    )
            except psutil.NoSuchProcess:
                pass
        living = []
        for pid, started in identities.items():
            try:
                process = psutil.Process(pid)
                if (
                    process.create_time() == started
                    and process.status() != psutil.STATUS_ZOMBIE
                ):
                    living.append(process)
                    process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(living, timeout=15)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass

    def launch(label):
        log = (folder / (label + ".log")).open("a")
        log_handles.append(log)
        p = subprocess.Popen(
            commands[label],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        processes[label] = p
        remember(p, label)
        event("started", role=label, pid=p.pid, command=commands[label])

    def interrupted(signum, frame):
        raise InterruptedError("Supervisor signal " + str(signum))

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    stage, failure = "running", None
    try:
        # The previous Teacher is allowed to finish in its original state
        # namespace. Its PID identity is enrolled for deadline cleanup only.
        for path in (output / "evolve/generation").glob("*/background_job.json"):
            external = read_json(path).get("external_process")
            if external:
                try:
                    p = psutil.Process(external["pid"])
                    if p.create_time() == external["create_time"]:
                        known[p.pid] = p.create_time()
                except psutil.NoSuchProcess:
                    pass
        for path in (output / "evolve/generation").glob("*/handoff_process.json"):
            for identity in read_json(path)["processes"]:
                try:
                    p = psutil.Process(identity["pid"])
                    if p.create_time() == identity["create_time"]:
                        known[p.pid] = p.create_time()
                except psutil.NoSuchProcess:
                    pass
        launch("teacher")
        launch("student")
        while time.time() < deployment["deadline_epoch"]:
            state_path = output / "evolve/state.json"
            state = read_json(state_path)
            progress = (
                len(state["groups"]),
                state["optimizer_steps"],
                len(state["pool"]),
            )
            if progress != last_progress:
                if last_progress is None or progress[:2] != last_progress[:2]:
                    restarts["student"] = 0
                last_progress = progress
                report(
                    output / "evolve",
                    state,
                    read_json(output / "baseline/results.json"),
                )
                event(
                    "progress",
                    groups=progress[0],
                    optimizer_steps=progress[1],
                    pool=progress[2],
                )
            for label, p in list(processes.items()):
                remember(p, label)
                if p.poll() is None or label in disabled:
                    continue
                if p.returncode == 0:
                    disabled[label] = "completed"
                    event("completed", role=label)
                    continue
                tail = (folder / (label + ".log")).read_text()[-12000:]
                status = (
                    read_json(output / "status.json")
                    if label == "student" and (output / "status.json").exists()
                    else {}
                )
                error_type = status.get("error_type", "ProcessExit")
                if label == "student" and error_type == "CalledProcessError":
                    logs = list((output / "evolve/updates").glob("*/attempt_*.log"))
                    if logs:
                        tail += max(logs, key=lambda p: p.stat().st_mtime).read_text()[
                            -12000:
                        ]
                    if "ValueError:" in tail or "AssertionError:" in tail:
                        error_type = "ValueError"
                # Preserve invariant failures; transient process/service failures
                # resume the existing receipts, never re-execute paid slots.
                can_retry = (
                    retryable(
                        error_type,
                        status.get("reason", "") + tail,
                        deployment["deadline_epoch"] - time.time(),
                    )
                    and restarts[label] < 3
                )
                event(
                    "process_exit",
                    role=label,
                    code=p.returncode,
                    retry=can_retry,
                    error_type=error_type,
                )
                if can_retry:
                    restarts[label] += 1
                    stop_owned(owned[label])
                    owned[label] = {}
                    if label == "student":
                        runtime.stop()
                    launch(label)
                else:
                    disabled[label] = "failed; see immutable receipts and logs"
            value = {
                "stage": "running",
                "phase": "student_and_teacher_parallel",
                "updated_epoch": time.time(),
                "deadline_epoch": deployment["deadline_epoch"],
                "student_pid": processes["student"].pid,
                "teacher_pid": processes["teacher"].pid,
                "groups": progress[0],
                "optimizer_steps": progress[1],
                "pool": progress[2],
                "disabled": disabled,
                "restarts_since_progress": restarts,
            }
            atomic(root / "supervisor.json", value)
            atomic(folder / "status.json", value)
            if len(disabled) == 2:
                stage = (
                    "complete"
                    if all(v == "completed" for v in disabled.values())
                    else "stopped"
                )
                break
            time.sleep(10)
        else:
            stage = "stopped_at_authorized_deadline"
    except BaseException as exc:
        stage, failure = "stopped", {"type": type(exc).__name__, "reason": str(exc)}
        raise
    finally:
        for p in processes.values():
            remember(p)
        stop_owned()
        runtime.stop()
        atomic(
            root / "supervisor.json",
            {
                "stage": stage,
                "updated_epoch": time.time(),
                "deadline_epoch": deployment["deadline_epoch"],
                "failure": failure,
                "disabled": disabled,
            },
        )
        event("supervisor_stopped", stage=stage, failure=failure)
        for log in log_handles:
            log.close()
        lock.close()


if __name__ == "__main__":
    main()
