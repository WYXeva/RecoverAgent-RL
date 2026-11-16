"""Background Teacher uses immutable feedback; Student state has one writer."""

import copy
import json
from pathlib import Path

import pytest

from recoveragent.io import digest, read_json, write_json
from recoveragent.s2_background import collect, enqueue, process_job, publish_result
from recoveragent import s2_background
from test_s2_experiment import cfg, FakeRuntime, generated
from recoveragent.s2_experiment import Experiment


def experiment(path, runtime, mode):
    return Experiment(
        runtime.cfg,
        {"pool": [generated("old")], "archive_sha256": "source", "feedback": []},
        {"s0": "S0"},
        path,
        mode,
        runtime,
    )


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", "v2_s2_background_20261117")
    runtime = FakeRuntime(cfg())
    runtime.prepare_rollouts = lambda actor: None
    run = experiment(tmp_path, runtime, mode="evolve")
    for _ in range(8):
        run.group()
    return run


def result(event, task):
    return {
        "event_sha256": digest(event),
        "accepted": [task],
        "candidate_count": 1,
        "context_completed": True,
        "explorations": 2,
        "attempts": [],
        "lineage": {"source_task_id": event["task_id"], "group_id": event["group_id"]},
        "teacher_usage_after": {"requests": 3},
        "error": None,
    }


def test_student_enqueues_frozen_four_histories_and_keeps_running(run, monkeypatch):
    monkeypatch.setattr(run.runtime, "stop", lambda: pytest.fail("Teacher stopped GPU"))
    run.feedback()
    path = run.state["background_jobs"]["8"]
    original = Path(path).read_bytes()
    job = read_json(path)
    assert all(len(e["episodes"]) == 4 for e in job["selected"])
    assert {e["group_id"] for e in job["selected"]} <= set(range(8))
    for _ in range(8):
        run.group()
    run.feedback()
    assert len(run.runtime.calls) == 64
    assert Path(path).read_bytes() == original
    assert set(run.state["background_jobs"]) == {"8", "16"}
    assert all(
        e["group_id"] >= 8
        for e in read_json(run.state["background_jobs"]["16"])["selected"]
    )


def test_worker_publishes_without_writing_student_state_then_next_slot_uses_task(
    run, monkeypatch
):
    enqueue(run)
    job = read_json(run.state["background_jobs"]["8"])
    before = (run.output / "state.json").read_bytes()
    task = generated("000_new", job["selected"][0]["task_id"])

    class Engine:
        def __init__(self, *args, **kwargs):
            pass

        def usage(self):
            return {"requests": 3}

        def run_context(self, event, directory):
            value = result(event, task)
            write_json(directory / "s2_context_result.json", value)
            return value

    monkeypatch.setattr(s2_background, "GroupEvolution", Engine)
    process_job(job, run.cfg, run.deployment, run.bundle, 1e20)
    assert (run.output / "state.json").read_bytes() == before
    collect(run)
    assert sum(t["task_id"] == task["task_id"] for t in run.state["pool"]) == 1
    after = (run.output / "state.json").read_bytes()
    collect(run)
    assert (run.output / "state.json").read_bytes() == after
    run.group()  # original slot 8
    run.group()  # generated priority slot 9
    assert (
        read_json(run.state["groups"][-1])["task"]["generated_task"]["task_id"]
        == "000_new"
    )
    assert "8" in run.state["feedback_windows"]


def test_pending_group_is_unchanged_by_arriving_teacher_result(run):
    enqueue(run)
    job = read_json(run.state["background_jobs"]["8"])
    task = generated("arriving", job["selected"][0]["task_id"])
    path = Path(job["contexts"][0]) / "s2_context_result.json"
    write_json(path, result(job["selected"][0], task))
    publish_result(job["directory"], 0, path)
    run.state["pending"] = {"task": {"task_id": "already_selected"}, "policy": "fixed"}
    before = copy.deepcopy(run.state)
    collect(run)
    assert run.state == before
    run.state["pending"] = None
    collect(run)
    assert any(t["task_id"] == "arriving" for t in run.state["pool"])


def test_restart_imports_once_and_rejects_changed_or_wrong_evidence(run):
    enqueue(run)
    job = read_json(run.state["background_jobs"]["8"])
    path = Path(job["contexts"][0]) / "s2_context_result.json"
    value = result(job["selected"][0], generated("one"))
    write_json(path, value)
    publish_result(job["directory"], 0, path)
    collect(run)
    resumed = experiment(run.output, run.runtime, mode="evolve")
    collect(resumed)
    assert len(resumed.state["pool"]) == len(run.state["pool"])
    value["event_sha256"] = "wrong"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="receipt changed"):
        collect(resumed)
    publish_result(job["directory"], 0, path)
    with pytest.raises(ValueError, match="receipt changed"):
        collect(resumed)


def test_finished_student_does_not_start_new_generation(run, monkeypatch):
    enqueue(run)
    job = read_json(run.state["background_jobs"]["8"])
    write_json(
        run.output / "generation/student_finished.json", {"completed_groups": 200}
    )
    monkeypatch.setattr(
        s2_background.GroupEvolution,
        "run_context",
        lambda *a: pytest.fail("Teacher started after training"),
    )
    process_job(job, run.cfg, run.deployment, run.bundle, 1e20)
    assert (
        read_json(Path(job["directory"]) / "cancelled.json")["reason"]
        == "student_training_finished"
    )


def test_background_job_detects_configuration_mismatch(run):
    enqueue(run)
    path = Path(run.state["background_jobs"]["8"])
    job = read_json(path)
    job["config_sha256"] = "different"
    path.write_text(json.dumps(job))
    with pytest.raises(ValueError, match="config changed"):
        collect(run)


def test_training_cuda_oom_skips_update_preserves_parent_and_resume(
    tmp_path, monkeypatch
):
    import subprocess
    import time
    from recoveragent.s2_backend import BackendRuntime
    from test_s2_experiment import make_group

    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", "v2_s2_background_20261117")
    runtime = BackendRuntime(
        cfg(),
        {"deadline_epoch": time.time() + 3600, "training_python": "synthetic"},
        tmp_path / "gpu",
    )
    monkeypatch.setattr(runtime, "stop", lambda: None)
    request = {
        "groups": [
            make_group(tmp_path, "a", [True, False, False, False]),
            make_group(tmp_path, "b", [False] * 4),
        ],
        "group_ids": [0, 1],
        "actor": "verified_parent",
        "s0": "S0",
        "optimizer": "verified_optimizer",
        "optimizer_sha256": "hash",
        "optimizer_steps": 4,
    }
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        kwargs["stdout"].write("torch.OutOfMemoryError: CUDA out of memory\n")
        kwargs["stdout"].flush()
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", fail)
    value = runtime.update(request, tmp_path / "update")
    assert value["updated"] is False and value["optimizer_steps"] == 4
    assert (
        value["actor"] == "verified_parent"
        and value["optimizer"] == "verified_optimizer"
    )
    assert value["reason"] == "cuda_oom_skipped_update"
    assert runtime.update(request, tmp_path / "update") == value and len(calls) == 1

    def invalid(command, **kwargs):
        kwargs["stdout"].write("ValueError: Nonfinite gradient\n")
        kwargs["stdout"].flush()
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", invalid)
    with pytest.raises(subprocess.CalledProcessError):
        runtime.update(request, tmp_path / "different_update")


def test_inference_oom_is_unknown_then_service_restarts_at_group_boundary(
    tmp_path, monkeypatch
):
    import time
    from recoveragent import s2_backend

    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", "v2_s2_background_20261117")
    runtime = s2_backend.BackendRuntime(
        cfg(),
        {
            "deadline_epoch": time.time() + 3600,
            "worker_python": "synthetic",
            "appworld_root": "synthetic",
        },
        tmp_path / "gpu",
    )
    runtime.tokenizer = "synthetic"
    monkeypatch.setattr(runtime, "policy", lambda actor: {"checkpoint_id": actor})
    monkeypatch.setattr(runtime, "serve", lambda actor: "backend")

    def oom(*args, **kwargs):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(s2_backend, "episode", oom)
    value = runtime.rollout(
        "parent", {"task_id": cfg()["feedback_task_ids"][0]}, tmp_path / "episode", 1
    )
    assert (
        value["success"] is None and value["classification"] == "infrastructure_error"
    )
    assert runtime.restart_after_group
    stopped = []
    monkeypatch.setattr(runtime, "stop", lambda: stopped.append(True))
    runtime.prepare_rollouts("parent")
    assert stopped == [True] and runtime.restart_after_group is False


def test_external_teacher_publishes_completed_context_but_no_second_budget_writer(
    run, monkeypatch
):
    enqueue(run)
    path = Path(run.state["background_jobs"]["8"])
    job = read_json(path)
    job["external_process"] = {"pid": 123, "create_time": 1}
    path.write_text(json.dumps(job))
    target = Path(job["contexts"][0]) / "s2_context_result.json"
    write_json(target, result(job["selected"][0], generated("external")))
    monkeypatch.setattr(s2_background, "external_active", lambda job: True)
    monkeypatch.setattr(
        s2_background,
        "process_job",
        lambda *args: pytest.fail("Concurrent Teacher budget writer"),
    )

    def end(*args):
        raise KeyboardInterrupt("one polling cycle")

    monkeypatch.setattr(s2_background.time, "sleep", end)
    with pytest.raises(KeyboardInterrupt):
        s2_background.worker(
            run.cfg,
            {"deadline_epoch": s2_background.time.time() + 3600},
            run.bundle,
            run.output,
        )
    collect(run)
    assert any(t["task_id"] == "external" for t in run.state["pool"])


def test_supervisor_resumes_transient_exit_but_not_invalid_training_or_deadline():
    from scripts.supervise_s2_background import retryable

    assert retryable("ConnectionError", "service unavailable", 3600)
    assert retryable("RuntimeError", "vLLM exited", 3600)
    assert not retryable("ValueError", "nonfinite gradient", 3600)
    assert not retryable("RuntimeError", "reload failed", 3600)
    assert not retryable("ConnectionError", "service unavailable", 1200)


def test_enqueue_crash_before_state_commit_keeps_original_job_pool(run):
    enqueue(run)
    path = Path(run.state["background_jobs"]["8"])
    before = path.read_bytes()
    del run.state["background_jobs"]["8"]
    run.state["pool"].append(generated("arrived_after_enqueue"))
    enqueue(run)
    assert path.read_bytes() == before
    assert run.state["background_jobs"]["8"] == str(path)


def test_reviewed_task_imports_once_without_rewriting_original_then_is_sampled(run):
    enqueue(run)
    job = read_json(run.state["background_jobs"]["8"])
    context = Path(job["contexts"][0])
    original = result(job["selected"][0], generated("original"))
    write_json(context / "s2_context_result.json", original)
    publish_result(job["directory"], 0, context / "s2_context_result.json")
    before = (context / "s2_context_result.json").read_bytes()
    reviewed = result(job["selected"][0], generated("000_reviewed"))
    reviewed.update(candidate_count=0, explorations=0, corrected_candidates=1)
    path = context / "review/context_result.json"
    write_json(path, reviewed)
    publish_result(job["directory"], 0, path, prefix="review_context")
    collect(run)
    assert len(run.state["generation_reviews"]) == 1
    assert (context / "s2_context_result.json").read_bytes() == before
    after = (run.output / "state.json").read_bytes()
    collect(run)
    assert (run.output / "state.json").read_bytes() == after
    run.group()
    run.group()
    assert (
        read_json(run.state["groups"][-1])["task"]["generated_task"]["task_id"]
        == "000_reviewed"
    )


def test_global_external_writer_blocks_earlier_paid_review(run, monkeypatch):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", "v2_s2_review_20261117")
    enqueue(run)
    earlier = Path(run.state["background_jobs"]["8"])
    write_json(earlier.parent / "cancelled.json", {"reason": "old"})
    later = copy.deepcopy(read_json(earlier))
    later["external_process"] = {"pid": 1, "create_time": 1}
    write_json(earlier.parent.parent / "zz_external/background_job.json", later)
    monkeypatch.setattr(
        s2_background, "external_active", lambda job: bool(job.get("external_process"))
    )
    monkeypatch.setattr(
        s2_background,
        "process_job",
        lambda *a, **kw: pytest.fail("Concurrent budget writer"),
    )

    def stop(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(s2_background.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        s2_background.worker(
            run.cfg,
            {"deadline_epoch": s2_background.time.time() + 3600},
            run.bundle,
            run.output,
        )
    assert (
        read_json(run.output / "generation/worker_status.json")["stage"]
        == "waiting_external"
    )
