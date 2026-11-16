"""One CPU/API Teacher worker; only the Student controller writes learning state."""

import copy
import fcntl
import json
import time
from pathlib import Path

from .demonstrations import sha256
from .io import digest, read_json
from .s2_evolution import GroupEvolution
from .s2_schedule import select_contexts
from .s2_state import atomic, evidence


def enqueue_regeneration(experiment, window, attempt):
    """One explicitly requested new-policy generation; retain previous outcomes."""
    import re
    from .v2_runtime import s2_execution

    if (
        not s2_execution().get("curriculum_admission")
        or experiment.state["pending"] is not None
    ):
        raise ValueError(
            "New-policy regeneration requires its explicit execution and a group boundary"
        )
    if re.fullmatch(r"[a-z0-9_]{1,48}", attempt or "") is None:
        raise ValueError("Invalid regeneration attempt")
    original_path = experiment.state["feedback_windows"][str(window)]
    original = read_json(original_path)
    job = job_record(
        experiment.output,
        window,
        original["selected"],
        experiment.state["pool"],
        retry_id=attempt,
        original_batch=original_path,
    )
    job.update(
        config_sha256=digest(experiment.cfg),
        admission_execution=s2_execution()["revision"],
        user_requested_regeneration=True,
    )
    path = Path(job["directory"]) / "background_job.json"
    if path.exists():
        saved = read_json(path)
        if (
            saved["selected"] != job["selected"]
            or saved.get("admission_execution") != job["admission_execution"]
        ):
            raise ValueError("Regeneration identity changed")
    else:
        atomic(path, job)
    experiment.state.setdefault("background_jobs", {})[f"retry_{window}_{attempt}"] = (
        str(path)
    )
    experiment.save()
    return path


def job_record(output, end, events, pool, *, retry_id=None, original_batch=None):
    """Freeze four-trajectory feedback before later Student groups can change it."""
    output = Path(output).resolve()
    suffix = "" if retry_id is None else "_retry_" + retry_id
    directory = output / "generation" / (f"window_{end:03d}" + suffix)
    prefix = f"s2_{digest(str(output))[:12]}_w{end:03d}"
    return {
        "completed_groups": end,
        "selected": copy.deepcopy(events),
        "initial_pool": copy.deepcopy(pool),
        "directory": str(directory),
        "contexts": [
            str(directory / f"{prefix}_c{i}{suffix}") for i in range(len(events))
        ],
        "retry_id": retry_id,
        "original_batch": original_batch,
    }


def enqueue(experiment):
    state = experiment.state
    end = len(state["groups"])
    if (
        state["mode"] != "evolve"
        or not end
        or end % 8
        or end == 200
        or str(end) in state["feedback_windows"]
        or str(end) in state.get("background_jobs", {})
    ):
        return
    job = job_record(
        experiment.output,
        end,
        select_contexts(state["events"][end - 8 : end]),
        state["pool"],
    )
    job["config_sha256"] = digest(experiment.cfg)
    directory = Path(job["directory"])
    path = directory / "background_job.json"
    if path.exists():
        prior = read_json(path)
        if any(
            prior[k] != job[k]
            for k in (
                "selected",
                "config_sha256",
                "contexts",
                "completed_groups",
                "retry_id",
            )
        ):
            raise ValueError("Queued feedback identity changed")
    else:
        atomic(path, job)
    state.setdefault("background_jobs", {})[str(end)] = str(
        directory / "background_job.json"
    )
    experiment.save()


def publish_result(directory, index, result_path, *, prefix="context"):
    # The original synchronous worker writes result.json directly. Its tiny
    # write may still be in flight during a live handoff; retry the read later.
    try:
        read_json(result_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    atomic(
        Path(directory) / "published" / f"{prefix}_{index}.json",
        {"path": str(result_path), "sha256": sha256(result_path)},
    )


def collect(experiment):
    """Import immutable receipts at group boundaries; never touch a pending group."""
    state = experiment.state
    if state["mode"] != "evolve" or state["pending"] is not None:
        return
    changed = False
    for job_path in state.get("background_jobs", {}).values():
        job = read_json(job_path)
        if job["config_sha256"] != digest(experiment.cfg):
            raise ValueError("Background feedback config changed")
        directory = Path(job["directory"])
        for i, event in enumerate(job["selected"]):
            for prefix in ("context", "review_context"):
                publication = directory / "published" / f"{prefix}_{i}.json"
                if not publication.exists():
                    continue
                receipt = read_json(publication)
                if sha256(receipt["path"]) != receipt["sha256"]:
                    raise ValueError("Background Teacher receipt changed")
                key = str(publication)
                previous = state.setdefault("background_imports", {}).get(key)
                if previous is not None:
                    if previous != receipt["sha256"]:
                        raise ValueError("Imported Teacher receipt changed")
                    continue
                result = read_json(receipt["path"])
                if result["event_sha256"] != digest(event):
                    raise ValueError("Background Teacher used different group evidence")
                known = {t["task_id"]: t for t in state["pool"]}
                for task in result["accepted"]:
                    if task["task_id"] in known:
                        if known[task["task_id"]] != task:
                            raise ValueError("Generated task identity changed")
                        continue
                    state["pool"].append(task)
                    known[task["task_id"]] = task
                    state["lineage"][task["task_id"]] = {
                        **result["lineage"],
                        "round": (job["completed_groups"] - 1) // 40,
                        "admitted_before_group": len(state["groups"]),
                        "background_job": str(job_path),
                    }
                state["teacher_usage"] = result["teacher_usage_after"]
                state["background_imports"][key] = receipt["sha256"]
                if prefix == "review_context":
                    state.setdefault("generation_reviews", {})[key] = receipt["path"]
                changed = True
        done = directory / "published" / "batch.json"
        if done.exists():
            receipt = read_json(done)
            if sha256(receipt["path"]) != receipt["sha256"]:
                raise ValueError("Background batch receipt changed")
            target = (
                state["feedback_windows"]
                if job["retry_id"] is None
                else state.setdefault("feedback_regenerations", {})
            )
            key = (
                str(job["completed_groups"])
                if job["retry_id"] is None
                else directory.name
            )
            if key not in target:
                target[key] = receipt["path"]
                changed = True
    if changed:
        experiment.save()


def process_job(job, cfg, deployment, bundle, deadline, *, review_only=False):
    """Use the existing generator and admission, without a GPU runtime or state writer."""
    from .v2_round import isolated

    if job["config_sha256"] != digest(cfg):
        raise ValueError("Background Teacher config changed")
    directory = Path(job["directory"])
    if not review_only:
        evidence(
            directory / "selection.json",
            {
                "groups": list(
                    range(job["completed_groups"] - 8, job["completed_groups"])
                ),
                "selected": job["selected"],
            },
        )
    engine = GroupEvolution(
        cfg,
        cfg["models"][cfg["selected_model"]],
        {
            **deployment,
            "teacher_budget_amount": cfg["s2"]["teacher_budget"]["priced_cost"],
        },
        directory,
        {
            "teacher_usage_priced_" + cfg["teacher"]["pricing"]["currency"].lower(): 0,
            "feedback": bundle["feedback"],
        },
        deadline,
        isolated,
        budget_root=directory.parent,
    )
    pool = {t["task_id"]: t for t in job["initial_pool"]}
    # Include earlier accepted jobs for novelty even if the Student was updating
    # when this job was enqueued. These are public train task definitions only.
    for publication in directory.parent.glob("*/published/*context_*.json"):
        record = read_json(publication)
        if sha256(record["path"]) != record["sha256"]:
            raise ValueError("Published generation evidence changed")
        for task in read_json(record["path"])["accepted"]:
            pool[task["task_id"]] = task
    results = []
    for i, (event, context_path) in enumerate(
        zip(job["selected"], job["contexts"], strict=True)
    ):
        if time.monotonic() + 120 >= deadline:
            raise TimeoutError("Background Teacher reached the existing deadline")
        if (directory.parent / "student_finished.json").exists():
            atomic(
                directory / "cancelled.json",
                {"reason": "student_training_finished", "completed_contexts": i},
            )
            return
        engine.pool = copy.deepcopy(list(pool.values()))
        original_path = Path(context_path) / "s2_context_result.json"
        if review_only and not original_path.exists():
            continue
        result = (
            read_json(original_path)
            if review_only
            else engine.run_context(event, Path(context_path))
        )
        results.append(result)
        for task in result["accepted"]:
            pool[task["task_id"]] = task
        if not review_only:
            publish_result(directory, i, original_path)
        from .v2_runtime import s2_execution

        if s2_execution().get("review_generated_tasks") and not job.get(
            "user_requested_adjudication"
        ):
            from .s2_review import REVISION, review_context

            engine.pool = copy.deepcopy(list(pool.values()))
            reviewed = review_context(engine, event, Path(context_path), result)
            for task in reviewed["accepted"]:
                pool[task["task_id"]] = task
            publish_result(
                directory,
                i,
                Path(context_path) / REVISION / "context_result.json",
                prefix="review_context",
            )
    if review_only:
        return
    batch = {
        "completed_groups": job["completed_groups"],
        "selected": job["selected"],
        "contexts": results,
        "selected_contexts": len(job["selected"]),
        "actual_contexts": sum(r.get("context_completed", False) for r in results),
        "candidate_count": sum(r["candidate_count"] for r in results),
        "explorations": sum(r["explorations"] for r in results),
        "accepted": sum(len(r["accepted"]) for r in results),
        "teacher_usage_cumulative": engine.usage(),
        **(
            {
                "retry_of_window": job["completed_groups"],
                "original_batch": job["original_batch"],
            }
            if job["retry_id"] is not None
            else {}
        ),
    }
    evidence(directory / "batch.json", batch)
    atomic(
        directory / "published/batch.json",
        {
            "path": str(directory / "batch.json"),
            "sha256": sha256(directory / "batch.json"),
        },
    )


def external_active(job):
    import psutil

    identity = job.get("external_process")
    if identity is None:
        return False
    try:
        p = psutil.Process(identity["pid"])
        return (
            p.create_time() == identity["create_time"]
            and p.status() != psutil.STATUS_ZOMBIE
        )
    except psutil.NoSuchProcess:
        return False


def worker(cfg, deployment, bundle, output):
    root = Path(output) / "generation"
    root.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0, deployment["deadline_epoch"] - time.time())
    with (root / "teacher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while time.monotonic() + 120 < deadline:
            jobs = sorted(root.glob("*/background_job.json"))
            waiting_external = False
            # Inspect ALL predecessors before any paid review/generation, even if
            # a lexically earlier completed job is eligible for retrospective review.
            for path in jobs:
                job = read_json(path)
                if job["config_sha256"] != digest(cfg):
                    raise ValueError("Background Teacher config changed")
                handoff = path.parent / "handoff_process.json"
                live = external_active(job) or (
                    handoff.exists()
                    and any(
                        external_active({"external_process": p})
                        for p in read_json(handoff)["processes"]
                    )
                )
                if live:
                    waiting_external = True
                    for i, context in enumerate(job["contexts"]):
                        publish_result(
                            job["directory"],
                            i,
                            Path(context) / "s2_context_result.json",
                        )
            if waiting_external:
                atomic(
                    root / "worker_status.json",
                    {"stage": "waiting_external", "updated_epoch": time.time()},
                )
                time.sleep(2)
                continue
            for path in jobs:
                job = read_json(path)
                if job["config_sha256"] != digest(cfg):
                    raise ValueError("Background Teacher config changed")
                directory = Path(job["directory"])
                handoff = directory / "handoff_process.json"
                if handoff.exists() and any(
                    external_active({"external_process": p})
                    for p in read_json(handoff)["processes"]
                ):
                    waiting_external = True
                    break
                if (directory / "published/batch.json").exists() or (
                    directory / "cancelled.json"
                ).exists():
                    from .v2_runtime import s2_execution

                    if (
                        s2_execution().get("review_generated_tasks")
                        and not job.get("user_requested_adjudication")
                        and not (root / "student_finished.json").exists()
                        and any(
                            not (
                                directory / "published" / f"review_context_{i}.json"
                            ).exists()
                            for i in range(len(job["contexts"]))
                        )
                    ):
                        atomic(
                            root / "worker_status.json",
                            {
                                "stage": "reviewing",
                                "job": str(path),
                                "updated_epoch": time.time(),
                            },
                        )
                        process_job(
                            job, cfg, deployment, bundle, deadline, review_only=True
                        )
                    continue
                if job.get("external_process"):
                    for i, context in enumerate(job["contexts"]):
                        publish_result(
                            directory, i, Path(context) / "s2_context_result.json"
                        )
                    if external_active(job):
                        waiting_external = True
                        break  # one Teacher budget writer, including the live predecessor
                    if (directory / "batch.json").exists():
                        atomic(
                            directory / "published/batch.json",
                            {
                                "path": str(directory / "batch.json"),
                                "sha256": sha256(directory / "batch.json"),
                            },
                        )
                        continue
                if (root / "student_finished.json").exists():
                    atomic(
                        directory / "cancelled.json",
                        {
                            "reason": "student_training_finished",
                            "completed_contexts": 0,
                        },
                    )
                    continue
                atomic(
                    root / "worker_status.json",
                    {
                        "stage": "running",
                        "job": str(path),
                        "updated_epoch": time.time(),
                    },
                )
                process_job(job, cfg, deployment, bundle, deadline)
            if (root / "student_finished.json").exists() and not waiting_external:
                return
            atomic(
                root / "worker_status.json",
                {
                    "stage": "waiting_external"
                    if waiting_external
                    else "waiting_for_feedback",
                    "updated_epoch": time.time(),
                },
            )
            time.sleep(2)
