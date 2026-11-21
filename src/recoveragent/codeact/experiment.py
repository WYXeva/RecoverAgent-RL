"""Bounded CodeAct GRPO + CoEvolve, with resumable current-policy groups."""

import argparse
import copy
import fcntl
import os
import signal
import subprocess
import time
import traceback
from pathlib import Path

from recoveragent.coevolve_llm import validate_task
from recoveragent.demonstrations import sha256
from recoveragent.io import digest, read_json, write_json
from recoveragent.s2_schedule import GroupFeedback, TaskQueue, select_contexts
from recoveragent.teacher import read_credentials
from recoveragent.v2_grpo import group_advantages
from recoveragent.v2_tasks import context_for, plan
from recoveragent.v2_training import verify_checkpoint

from .collect import replace_json
from .episode import run
from .evolution import Engine, epoch, generate_batch, reward
from .inference import VLLMStudent, evaluate
from .online import REVISION, policy, update
from .progress import execution_identity
from .protocol import identity
from .sampling import (
    SamplingCapacityError,
    fallback_enabled,
    resource_error,
    sample_with_fallback,
)


def within_deadline(deployment):
    deadline = deployment["deadline"]
    return deadline is None or epoch() < deadline


def effective(episodes):
    try:
        values = group_advantages(episodes)
        return values is not None and any(values)
    except ValueError:
        return False


def sampling_limit_reached(config, count):
    limit = config["online"]["max_groups"]
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("max_groups must be a positive integer or null")
    return limit is not None and count >= limit


def generation_windows(config, state):
    if state["optimizer_steps"] >= config["online"]["max_updates"]:
        return range(0)
    end = len(state["groups"]) + 1
    limit = config["online"]["max_groups"]
    if limit is not None:
        end = min(end, limit)
    return range(8, end, config["online"]["feedback_every_groups"])


def validate_training_pool(config, s0):
    settings = config["online"].get("task_pool")
    if not settings:
        return
    frozen = plan()
    train = {r["task_id"] for r in frozen["rows"] if r["role"] == "train"}
    allowed = config["online"]["train_tasks"]
    excluded = settings["excluded_sft_tasks"]
    if (
        settings["s0_receipt_sha256"] != digest(s0)
        or settings["split_manifest_sha256"] != frozen["manifest_sha256"]
        or len(set(allowed)) != len(allowed)
        or len(set(excluded)) != len(excluded)
        or not set(excluded) <= train
        or set(allowed) != train - set(excluded)
    ):
        raise ValueError("SFT exclusion or train pool provenance mismatch")


def retire_ineligible_pending(config, state):
    allowed = set(config["online"]["train_tasks"])
    for index in list(state["pending"]):
        group = read_json(state["groups"][index])
        if (
            not group["selection"].get("generated_task")
            and group["selection"]["task_id"] not in allowed
        ):
            state["pending"].remove(index)
            state.setdefault("retired_pending", []).append(
                {"group_id": index, "reason": "source_task_excluded_by_sft_policy"}
            )


def evaluation_due(config, state):
    """Keep sampling windows separate from successful-update evaluation rounds."""
    settings = config["online"].get("round_evaluation")
    if not settings:
        return None
    interval = settings.get("every_optimizer_updates")
    if interval is not None:
        if type(interval) is not int or interval < 1:
            raise ValueError("Evaluation update interval must be a positive integer")
        count = state["optimizer_steps"]
        if not count or count % interval:
            return None
        due = {
            "evaluation_basis": "optimizer_updates",
            "round": count // interval,
            "target_updates": count,
        }
    else:
        count, size = len(state["groups"]), config["grpo"]["groups_per_round"]
        if not count or count % size:
            return None
        due = {"evaluation_basis": "sampled_groups", "round": count // size}
    if any(
        r.get("evaluation_basis", "sampled_groups") == due["evaluation_basis"]
        and r["round"] == due["round"]
        for r in state.get("round_evaluations", [])
    ):
        return None
    return due


def summary(state, deployment):
    groups = [read_json(p) for p in state["groups"]]
    rows = [e for g in groups for e in g["episodes"]]
    discarded = [
        e
        for g in groups
        for e in g.get("sampling_fallback", {}).get("discarded_attempts", [])
    ]
    sources = {}
    for g in groups:
        item = sources.setdefault(
            g["selection"]["source"], {"groups": 0, "varying": 0, "rewards": []}
        )
        item["groups"] += 1
        item["varying"] += int(effective(g["episodes"]))
        item["rewards"].append(
            [
                e["success"] if e.get("reward_valid", True) else None
                for e in g["episodes"]
            ]
        )
    return {
        "status": state["status"],
        "completed_groups": len(groups),
        "completed_rollouts": len(rows),
        "optimizer_steps": state["optimizer_steps"],
        "pending_groups": state["pending"],
        "generated_pool": len(state["pool"]),
        "round_evaluations": state.get("round_evaluations", []),
        "learning_round_progress": (
            {
                "basis": "optimizer_updates",
                "updates_per_round": state["updates_per_round"],
                "completed_rounds": state["optimizer_steps"]
                // state["updates_per_round"],
                "updates_in_round": state["optimizer_steps"]
                % state["updates_per_round"],
            }
            if state.get("updates_per_round")
            else None
        ),
        "success": sum(e["success"] is True for e in rows),
        "failure": sum(e["success"] is False for e in rows),
        "unknown": sum(e["success"] is None for e in rows),
        "sampling_retries": len(discarded),
        "discarded_sampling_usage_unknown": sum(
            e.get("usage_unknown", False) for e in discarded
        ),
        "sources": sources,
        "student_usage": {
            k: sum(e.get("usage", {}).get(k, 0) for e in rows + discarded)
            for k in ("requests", "prompt_tokens", "generated_tokens", "seconds")
        },
        "generation_batches": state["generation_batches"],
        "actor": state["actor"],
        "started": state["started"],
        "deadline": deployment["deadline"],
        "saved": epoch(),
    }


def sample_member(config, deployment, request, directory, member, *, interrupted=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    expected = policy(verify_checkpoint(request["actor"], config["model"]), config)
    generated = request["selection"].get("generated_task")
    task = request["selection"]["task_id"]
    context_for(task, training=True)
    if not generated and task not in config["online"]["train_tasks"]:
        raise ValueError("Sampling task is excluded from the current train pool")
    if generated:
        validate_task(generated)
    target = directory / f"member_{member}"
    if (target / "online_episode.json").exists():
        cached = read_json(target / "online_episode.json")
        if (
            cached["policy"] != expected
            or cached["task_id"] != task
            or cached["generated_task_id"]
            != (generated["task_id"] if generated else None)
            or cached["seed"] != config["seed"] + request["index"] * 4 + member
            or cached.get("student_execution") != execution_identity(config)
        ):
            raise ValueError("Cached rollout policy/task/seed differs")
        return cached
    cfg = copy.deepcopy(config)
    cfg["seed"] = config["seed"] + request["index"] * 4 + member
    row = {
        "task_id": task,
        "generated_task_id": generated["task_id"] if generated else None,
        "policy": expected,
        "seed": cfg["seed"],
        "directory": str(target),
        "success": None,
        "reward_valid": False,
        "initial_state_hash": None,
        "classification": "infrastructure_error",
        "fault": None,
        "student_execution": execution_identity(config),
        "worker_pid": os.getpid(),
        "started": epoch(),
        "failure_stage": "sampling",
    }
    if interrupted and not (
        (target / "trajectory.json").exists() and (target / "result.json").exists()
    ):
        live = target / "live.json"
        row.update(
            fault=interrupted,
            failure_type="SamplingCapacityError",
            usage=read_json(live).get("usage", {}) if live.exists() else {},
            usage_unknown=True,
            finished=epoch(),
        )
        write_json(target / "online_episode.json", row)
        return row
    try:
        if not within_deadline(deployment):
            raise TimeoutError("Authorized wall-clock deadline")
        if not target.exists():
            client = VLLMStudent(
                cfg,
                request["actor"],
                request["server"]["url"],
                request["server_receipt"],
            )
            run(
                task,
                member + 1,
                target,
                deployment["appworld_root"],
                cfg,
                client,
                generated_task=generated,
            )
        if not (target / "trajectory.json").exists():
            raise ValueError("Interrupted attempt preserved without resampling")
        trajectory = read_json(target / "trajectory.json")
        if trajectory.get("student_execution") != execution_identity(config):
            raise ValueError("Interrupted rollout execution settings differ")
        result = read_json(target / "result.json")
        write_history = target / "history.json"
        if not write_history.exists():
            write_json(write_history, {"messages": trajectory["messages"]})
        row.update(
            initial_state_hash=trajectory["initial_state_hash"],
            termination=result["termination"],
            usage=result["usage"],
            seconds=result["seconds"],
            fault=trajectory["fault"],
        )
        if trajectory["fault"]:
            row.update(success=None, reward_valid=False)
        elif generated:
            row["failure_stage"] = "judging"
            judged = reward(
                Engine(config, deployment, read_credentials(deployment["credentials"])),
                generated,
                trajectory,
                target / "judge",
            )
            row.update(
                success=judged["success"],
                reward_valid=judged["reward_valid"],
                classification="valid" if judged["reward_valid"] else "scoring_gap",
            )
        else:
            row["failure_stage"] = "evaluation"
            row.update(
                success=result["success"],
                reward_valid=result["valid"],
                classification="valid" if result["valid"] else "infrastructure_error",
            )
    except (ValueError, RuntimeError, OSError, TimeoutError, MemoryError) as exc:
        row.update(
            fault=str(exc),
            success=None,
            reward_valid=False,
            failure_type=type(exc).__name__,
        )
        if row["failure_stage"] == "sampling":
            live = target / "live.json"
            row.setdefault(
                "usage", read_json(live).get("usage", {}) if live.exists() else {}
            )
            row["usage_unknown"] = True
    row["finished"] = epoch()
    write_json(target / "online_episode.json", row)
    return row


def sampling_concurrency(config):
    count = config.get("online", {}).get("sampling_concurrency", 1)
    if type(count) is not int or not 1 <= count <= 4:
        raise ValueError("Sampling concurrency must be an integer from 1 to 4")
    return count


def sample_group(config, deployment, request, directory):
    """One actor, four fresh processes, at most N simultaneous environments."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    width = request.get("sampling_concurrency", sampling_concurrency(config))
    members = request.get("members", list(range(4)))
    if (
        type(width) is not int
        or width not in (1, 2, 3, 4)
        or not members
        or len(set(members)) != len(members)
        or any(type(i) is not int or i not in range(4) for i in members)
        or (
            (width != sampling_concurrency(config) or members != list(range(4)))
            and not fallback_enabled(config)
        )
    ):
        raise ValueError("Invalid sampling phase width/member selection")
    if request["server"].get("max_num_seqs", 1) < width:
        raise ValueError("vLLM server concurrency is below requested sampling width")
    started = epoch()
    try:
        if width == 1:
            episodes = [
                sample_member(config, deployment, request, directory, i)
                for i in members
            ]
        else:
            # AppWorld changes process globals. Each episode needs a fresh process.
            with ProcessPoolExecutor(
                max_workers=width,
                mp_context=multiprocessing.get_context("spawn"),
                max_tasks_per_child=1,
            ) as workers:
                futures = [
                    workers.submit(
                        sample_member, config, deployment, request, directory, i
                    )
                    for i in members
                ]
                episodes = [f.result() for f in futures]
    except (BrokenProcessPool, MemoryError, OSError) as exc:
        if isinstance(exc, OSError) and not resource_error(exc):
            raise
        write_json(
            directory / "phase_error.json",
            {
                "type": "SamplingCapacityError",
                "error": repr(exc),
            },
        )
        raise SamplingCapacityError(str(exc)) from exc
    result = {
        "index": request["index"],
        "selection": request["selection"],
        "episodes": episodes,
        "effective": len(episodes) == 4 and effective(episodes),
        "members": members,
        "sampling_concurrency": width,
        "student_execution": execution_identity(config),
        "sampling_wall_seconds": epoch() - started,
    }
    write_json(directory / "result.json", result)
    return result


class Server:
    def __init__(self, config, deployment, actor, directory, *, concurrency=None):
        import requests

        self.process = None
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        checkpoint = verify_checkpoint(actor, config["model"])
        self.proof = {
            "protocol": identity(),
            "base": config["model"],
            "adapter_receipt_sha256": digest(checkpoint),
            "max_model_len": 65536,
            "dtype": "bfloat16",
            "vllm_version": "0.19.0",
            "max_num_seqs": concurrency or sampling_concurrency(config),
            "url": "http://127.0.0.1:" + str(deployment["port"]),
            "model_name": "codeact_" + checkpoint["adapter_sha256_after"][:16],
            "actor": actor,
            "logprobs_mode": "processed_logprobs",
        }
        command = [
            deployment["vllm_python"],
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            deployment["base_path"],
            "--served-model-name",
            "Qwen3.5-9B-pinned",
            "--host",
            "127.0.0.1",
            "--port",
            str(deployment["port"]),
            "--dtype",
            "bfloat16",
            "--max-model-len",
            "65536",
            "--max-num-seqs",
            str(self.proof["max_num_seqs"]),
            "--max-num-batched-tokens",
            "4096",
            "--gpu-memory-utilization",
            "0.88",
            "--enable-prefix-caching",
            "--enable-chunked-prefill",
            "--enable-lora",
            "--max-lora-rank",
            "16",
            "--max-loras",
            "1",
            "--lora-target-modules",
            "qkv_proj",
            "o_proj",
            "gate_up_proj",
            "down_proj",
            "--lora-modules",
            self.proof["model_name"] + "=" + actor,
            "--logprobs-mode",
            "processed_logprobs",
            "--limit-mm-per-prompt",
            '{"image":0,"video":0}',
            "--generation-config",
            "vllm",
            "--no-enable-log-requests",
        ]
        write_json(self.directory / "command.json", command)
        with (self.directory / "server.log").open("x") as log:
            self.process = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
        http = requests.Session()
        http.trust_env = False
        until = epoch() + 600
        if deployment["deadline"] is not None:
            until = min(until, deployment["deadline"])
        try:
            while epoch() < until:
                if self.process.poll() is not None:
                    text = (self.directory / "server.log").read_text(errors="replace")
                    if resource_error(text) or self.process.returncode in (-9, 137):
                        raise SamplingCapacityError("vLLM capacity failure at startup")
                    raise RuntimeError("vLLM exited at startup")
                try:
                    response = http.get(self.proof["url"] + "/v1/models", timeout=5)
                    if response.ok and self.proof["model_name"] in {
                        r["id"] for r in response.json()["data"]
                    }:
                        self.receipt = self.directory / "receipt.json"
                        write_json(self.receipt, self.proof)
                        return
                except requests.RequestException:
                    pass
                time.sleep(2)
            raise TimeoutError("vLLM startup deadline")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()


def worker_command(deployment, kind, config_path, request_path, output):
    python = (
        deployment["training_python"]
        if kind == "update"
        else deployment["worker_python"]
    )
    return [
        python,
        "-m",
        "recoveragent.codeact.experiment",
        kind,
        "--config",
        str(config_path),
        "--deployment",
        deployment["deployment_file"],
        "--request",
        str(request_path),
        "--output",
        str(output),
    ]


def update_environment(config, env):
    """Set the allocator before CUDA initialization, only in the update worker."""
    setting = config["grpo"].get("cuda_allocator")
    if setting is not None:
        if not isinstance(setting, str) or not setting.strip():
            raise ValueError("Invalid training CUDA allocator setting")
        env.pop("PYTORCH_CUDA_ALLOC_CONF", None)
        env["PYTORCH_ALLOC_CONF"] = setting
    return env


def launch(deployment, kind, config_path, request_path, output):
    output = Path(output)
    env = dict(os.environ)
    if kind == "update":
        update_environment(read_json(config_path), env)
        env["PYTHONPATH"] = (
            deployment["kernel_overlay"] + os.pathsep + env["PYTHONPATH"]
        )
    with output.with_suffix(".log").open("a") as log:
        return subprocess.Popen(
            worker_command(deployment, kind, config_path, request_path, output),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def wait_process(process, deadline):
    timeout = None if deadline is None else max(0.01, deadline - epoch())
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise TimeoutError("Job deadline; original actor and pending groups preserved")
    if code:
        raise RuntimeError(f"Worker exited {code}; inspect its log")


def process_identity(pid):
    """Bind an inherited worker to its PID lifetime and exact command."""
    root = Path("/proc") / str(pid)
    try:
        stat = (root / "stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return digest(
            {"start_ticks": stat[19], "command": (root / "cmdline").read_bytes().hex()}
        )
    except FileNotFoundError:
        return None


class InheritedGeneration:
    """Wait for the existing Teacher process; never launch its work twice."""

    def __init__(self, binding, output):
        self.pid, self.identity = binding["pid"], binding["identity"]
        self.output, self.returncode = Path(output), None

    def poll(self):
        if (self.output / "result.json").exists():
            self.returncode = 0
        elif process_identity(self.pid) != self.identity:
            self.returncode = 1
        return self.returncode

    def wait(self, timeout):
        until = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if until is not None and time.monotonic() >= until:
                raise subprocess.TimeoutExpired("inherited Teacher", timeout)
            time.sleep(0.2)
        return self.returncode


def resilient_group(config, deployment, config_path, request, target, ensure_server):
    """Run one task at four, with at most one task-local two-way capacity retry."""
    target = Path(target)
    started = epoch()

    def check_deadline():
        if not within_deadline(deployment):
            raise TimeoutError("Authorized sampling deadline; no retry beyond budget")

    def execute(width, members, path):
        server = ensure_server(width)
        req = {
            **request,
            "server": server.proof,
            "server_receipt": str(server.receipt),
            "sampling_concurrency": width,
            "members": members,
        }
        rp = path.with_suffix(".request.json")
        write_json(rp, req)
        process = launch(deployment, "sample", config_path, rp, path)
        try:
            wait_process(process, deployment["deadline"])
        except RuntimeError as exc:
            fault = path / "phase_error.json"
            if (
                fault.exists()
                and read_json(fault).get("type") == "SamplingCapacityError"
            ) or process.returncode in (-9, 137):
                # A killed group worker can leave child worlds alive. Stop that
                # isolated process group before reading partials or starting retry.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise SamplingCapacityError(str(exc)) from exc
            raise
        return read_json(path / "result.json")["episodes"]

    def recover(path, members, fault):
        # Completed receipts are reused, including valid failures and judge gaps.
        # Incomplete records get an explicit invalid outcome; original files stay put.
        return [
            sample_member(config, deployment, request, path, i, interrupted=fault)
            for i in members
        ]

    binding = {
        "index": request["index"],
        "actor": request["actor"],
        "selection_sha256": digest(request["selection"]),
        "config_sha256": digest(config),
    }
    outcome = sample_with_fallback(target, binding, execute, recover, check_deadline)
    rows = outcome["episodes"]
    expected = policy(verify_checkpoint(request["actor"], config["model"]), config)
    for i, row in enumerate(rows):
        if (
            row["policy"] != expected
            or row["seed"] != config["seed"] + request["index"] * 4 + i
        ):
            raise ValueError("Fallback changed actor or member seed")
    hashes = {r["initial_state_hash"] for r in rows if r.get("reward_valid")}
    if len(hashes) > 1:
        raise ValueError("Fallback initial environment differs")
    result = {
        **outcome,
        "index": request["index"],
        "selection": request["selection"],
        "effective": effective(rows),
        "student_execution": execution_identity(config),
        "sampling_wall_seconds": epoch() - started,
    }
    write_json(target / "result.json", result)
    return result


def experiment(config, deployment, config_path, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    lock = (output / "controller.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state_path = output / "state.json"
    use_fallback = fallback_enabled(config)
    s0 = verify_checkpoint(deployment["s0"], config["model"])
    sampling_limit_reached(config, 0)
    validate_training_pool(config, s0)
    if (
        s0.get("protocol") != identity()
        or not s0.get("formal_s0")
        or s0["adapter_sha256_after"] != config["online"]["s0_sha256"]
    ):
        raise ValueError("New CodeAct common S0 identity mismatch")
    binding = {
        "revision": REVISION,
        "protocol": identity(),
        "config_sha256": digest(config),
        "s0_receipt_sha256": digest(s0),
        "deployment": deployment,
    }
    if (output / "binding.json").exists():
        if read_json(output / "binding.json") != binding:
            raise ValueError("CodeAct online run binding changed")
        state = read_json(state_path)
    else:
        write_json(output / "binding.json", binding)
        state = {
            "status": "running",
            "actor": deployment["s0"],
            "optimizer": None,
            "optimizer_sha256": None,
            "optimizer_steps": 0,
            "groups": [],
            "pending": [],
            "consumed": [],
            "pool": [],
            "events": [],
            "feedback": None,
            "queue": None,
            "generation_batches": [],
            "update_attempts": [],
            "round_evaluations": [],
            "active": None,
            "started": epoch(),
        }
        replace_json(state_path, state)
    state.setdefault("round_evaluations", [])
    interval = (
        config["online"].get("round_evaluation", {}).get("every_optimizer_updates")
    )
    if interval is not None:
        evaluation_due(config, state)  # Validate before launching any workers.
        state["updates_per_round"] = interval
    queue = TaskQueue(config["online"]["train_tasks"], config["seed"], state["queue"])
    feedback = GroupFeedback(config["online"]["signals"], state["feedback"])
    server, background = None, None
    inherited = [
        b
        for b in state["generation_batches"]
        if b.get("running") and b.get("inherited_process") and not b.get("merged")
    ]
    if len(inherited) > 1:
        raise ValueError("Only one existing Teacher generation worker can be inherited")
    if inherited:
        b = inherited[0]
        background = InheritedGeneration(b["inherited_process"], b["output"])

    def save():
        state["queue"], state["feedback"] = queue.export(), feedback.state
        state["last_saved"] = epoch()
        replace_json(state_path, state)
        replace_json(output / "summary.json", summary(state, deployment))

    def ensure_sampling_server(width):
        nonlocal server
        if server is not None and server.proof.get("max_num_seqs") != width:
            server.close()
            server = None
        if server is None:
            server = Server(
                config,
                deployment,
                state["actor"],
                output / "servers" / str(time.time_ns()),
                concurrency=width,
            )
        return server

    def collect_generation():
        for batch in state["generation_batches"]:
            result = Path(batch["output"]) / "result.json"
            if not batch.get("merged") and result.exists():
                done = read_json(result)
                existing = {t["task_id"] for t in state["pool"]}
                for task in done["accepted"]:
                    validate_task(task)
                    if task["task_id"] not in existing:
                        state["pool"].append(task)
                        existing.add(task["task_id"])
                batch.update(
                    merged=True,
                    candidates=len(done["attempts"]),
                    accepted=len(done["accepted"]),
                )
                save()

    def pump_generation():
        nonlocal background
        collect_generation()
        existing = {b["window"] for b in state["generation_batches"]}
        for count in generation_windows(config, state):
            if count in existing:
                continue
            contexts = select_contexts(
                state["events"][count - 8 : count],
                eligible_originals=set(config["online"]["train_tasks"]),
            )
            destination = output / "generation" / f"window_{count:03d}"
            request_path = destination.with_suffix(".request.json")
            if not request_path.exists():
                write_json(request_path, {"contexts": contexts, "pool": state["pool"]})
            state["generation_batches"].append(
                {
                    "window": count,
                    "output": str(destination),
                    "request": str(request_path),
                    "contexts": len(contexts),
                    "merged": False,
                }
            )
            save()
        if background is not None and background.poll() is not None:
            if background.returncode:
                active = next(
                    b for b in state["generation_batches"] if b.get("running")
                )
                active.update(
                    error=f"Teacher worker exited {background.returncode}", failed=True
                )
            for b in state["generation_batches"]:
                b.pop("running", None)
            background = None
            save()
        if background is None and within_deadline(deployment):
            for batch in state["generation_batches"]:
                if not batch["merged"] and not batch.get("failed"):
                    background = launch(
                        deployment,
                        "evolve",
                        config_path,
                        batch["request"],
                        Path(batch["output"]),
                    )
                    batch["running"] = True
                    save()
                    break

    def apply_update():
        nonlocal server
        ids = state["pending"][:2]
        if not ids:
            return
        if server:
            server.close()
            server = None
        target = output / "updates" / f"attempt_{len(state['update_attempts']):03d}"
        req = {
            "groups": [read_json(state["groups"][i])["episodes"] for i in ids],
            "group_ids": ids,
            "actor": state["actor"],
            "s0": deployment["s0"],
            "optimizer": state["optimizer"],
            "optimizer_sha256": state["optimizer_sha256"],
            "optimizer_steps": state["optimizer_steps"],
        }
        req_path = target.with_suffix(".request.json")
        if not req_path.exists():
            write_json(req_path, req)
        elif read_json(req_path) != req:
            raise ValueError("Unconsumed update request changed")
        if not (target / "result.json").exists():
            if target.exists():
                target.rename(
                    target.with_name(target.name + f".interrupted_{time.time_ns()}")
                )
            process = launch(deployment, "update", config_path, req_path, target)
            wait_process(process, deployment["deadline"])
        result = read_json(target / "result.json")
        if result["group_ids"] != ids or result["optimizer_steps"] != state[
            "optimizer_steps"
        ] + int(result["updated"]):
            raise ValueError("Update receipt counter/group mismatch")
        state.update(
            {
                k: result[k]
                for k in ("actor", "optimizer", "optimizer_sha256", "optimizer_steps")
            }
        )
        state["pending"] = state["pending"][len(ids) :]
        state["update_attempts"].append(str(target / "result.json"))
        if result["updated"]:
            state["consumed"].extend(ids)
        save()

    def evaluate_round():
        nonlocal server
        due = evaluation_due(config, state)
        if due is None:
            return False
        if (
            due["evaluation_basis"] == "sampled_groups"
            and state["pending"]
            and state["optimizer_steps"] < config["online"]["max_updates"]
        ):
            apply_update()  # Flush the last valid group before the round's evaluation.
        name = (
            f"updates_{due['target_updates']:06d}"
            if due["evaluation_basis"] == "optimizer_updates"
            else f"round_{due['round']:02d}"
        )
        target = output / "evaluations" / name
        if server is None:
            server = Server(
                config,
                deployment,
                state["actor"],
                output / "servers" / str(time.time_ns()),
            )
        request = {
            **due,
            "actor": state["actor"],
            "server": server.proof,
            "server_receipt": str(server.receipt),
        }
        request_path = target.with_suffix(".request.json")
        if request_path.exists():
            previous = read_json(request_path)
            if (
                previous["actor"] != request["actor"]
                or previous["server"] != request["server"]
            ):
                raise ValueError("Round evaluation policy changed")
            request_path = target.with_suffix(f".resume_{time.time_ns()}.request.json")
        write_json(request_path, request)
        state["status"] = "evaluating"
        save()
        process = launch(deployment, "evaluate", config_path, request_path, target)
        wait_process(process, deployment["deadline"])
        result = read_json(target / "summary.json")
        if result["status"] != "complete" or result["tasks"] != 24:
            raise ValueError("Round dev24 incomplete; do not advance the round")
        state["round_evaluations"].append(
            {
                **due,
                "actor": state["actor"],
                "directory": str(target),
                "optimizer_steps": state["optimizer_steps"],
                "summary": result,
            }
        )
        state["status"] = "running"
        save()
        return True

    try:
        while within_deadline(deployment):
            pump_generation()
            if evaluate_round():
                continue
            if (
                sampling_limit_reached(config, len(state["groups"]))
                or state["optimizer_steps"] >= config["online"]["max_updates"]
            ):
                break
            if len(state["pending"]) >= 2:
                apply_update()
                continue
            index = len(state["groups"])
            if not state["active"]:
                size = config["grpo"]["groups_per_round"]
                state["active"] = queue.take(
                    index // size,
                    index % size,
                    state["pool"],
                    set(),
                )
                save()
            if use_fallback:
                target = output / "groups" / f"group_{index:03d}"
                if not (target / "result.json").exists():
                    resilient_group(
                        config,
                        deployment,
                        config_path,
                        {
                            "index": index,
                            "actor": state["actor"],
                            "selection": state["active"],
                        },
                        target,
                        ensure_sampling_server,
                    )
                group = read_json(target / "result.json")
                state["groups"].append(str(target / "result.json"))
                state["events"].append(feedback.observe(str(target), group["episodes"]))
                if effective(group["episodes"]):
                    state["pending"].append(index)
                state["active"] = None
                save()
                pump_generation()
                continue
            if server is None:
                server = Server(
                    config,
                    deployment,
                    state["actor"],
                    output / "servers" / str(time.time_ns()),
                )
            target = output / "groups" / f"group_{index:03d}"
            req = {
                "index": index,
                "actor": state["actor"],
                "selection": state["active"],
                "server": server.proof,
                "server_receipt": str(server.receipt),
            }
            req_path = target.with_suffix(".request.json")
            if not req_path.exists():
                write_json(req_path, req)
            else:
                old = read_json(req_path)
                if any(old[k] != req[k] for k in ("index", "actor", "selection")):
                    raise ValueError("Pending sampling task or actor changed")
                # A restarted server has a new receipt; preserve the original request.
                req_path = target.with_suffix(f".resume_{time.time_ns()}.request.json")
                write_json(req_path, req)
            if not (target / "result.json").exists():
                process = launch(deployment, "sample", config_path, req_path, target)
                wait_process(process, deployment["deadline"])
            group = read_json(target / "result.json")
            state["groups"].append(str(target / "result.json"))
            state["events"].append(feedback.observe(str(target), group["episodes"]))
            if effective(group["episodes"]):
                state["pending"].append(index)
            state["active"] = None
            save()
            pump_generation()
        if (
            state["pending"]
            and state["optimizer_steps"] < config["online"]["max_updates"]
            and within_deadline(deployment)
        ):
            apply_update()
            if interval is not None and within_deadline(deployment):
                evaluate_round()  # A final single-group update may reach a milestone.
        state["status"] = (
            "complete"
            if sampling_limit_reached(config, len(state["groups"]))
            or state["optimizer_steps"] >= config["online"]["max_updates"]
            else "deadline"
        )
    except TimeoutError as exc:
        state.update(status="deadline", error=str(exc))
    except BaseException as exc:
        state.update(status="blocked", error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        if server:
            server.close()
        while background is not None:
            try:
                wait_process(background, deployment["deadline"])
                pump_generation()
                if not within_deadline(deployment):
                    break
            except (RuntimeError, TimeoutError) as exc:
                state["generation_error"] = str(exc)
                break
        collect_generation()
        state["finished"] = epoch()
        save()
        lock.close()


def verify_source(deployment):
    root = Path(deployment["source_root"])
    manifest = read_json(
        root
        / deployment.get(
            "source_lock", "configs/codeact_online_source_20261119.lock.json"
        )
    )
    for name, expected in manifest["files"].items():
        if not (root / name).is_file() or sha256(root / name) != expected:
            raise ValueError("CodeAct online source changed: " + name)
    return digest(manifest)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument(
        "command", choices=["run", "sample", "update", "evolve", "evaluate"]
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--request")
    args = parser.parse_args()
    config, deployment = read_json(args.config), read_json(args.deployment)
    verify_source(deployment)
    from recoveragent.evaluation import CLASSIFICATION_REVISION

    if (
        config.get("official_evaluation_revision", CLASSIFICATION_REVISION)
        != CLASSIFICATION_REVISION
    ):
        raise ValueError("Official evaluation classification revision differs")
    if os.environ.get("RECOVERAGENT_S2_EXECUTION"):
        raise ValueError("Old S2 execution environment must not enter CodeAct run")
    if args.command == "run":
        experiment(config, deployment, args.config, args.output)
    else:
        request = read_json(args.request)
        if args.command == "sample":
            sample_group(config, deployment, request, args.output)
        elif args.command == "evaluate":
            settings = config["online"]["round_evaluation"]
            cfg = copy.deepcopy(config)
            cfg["seed"] = settings["seed"]
            cfg["student_inference"] = settings["student_inference"]
            cfg["evaluation_resume_interrupted_as_unknown"] = True
            evaluate(
                cfg,
                args.output,
                deployment["appworld_root"],
                request["actor"],
                "dev",
                task_set="dev24",
                vllm_url=request["server"]["url"],
                server_receipt=request["server_receipt"],
            )
        elif args.command == "update":
            update_environment(config, os.environ)
            import torch

            try:
                update(config, request, args.output)
            except torch.cuda.OutOfMemoryError as exc:
                # Skip the whole update after OOM, never train a partial group.
                write_json(
                    Path(args.output) / "result.json",
                    {
                        "updated": False,
                        "reason": "cuda_oom_whole_batch_skipped",
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                        **{
                            k: request[k]
                            for k in (
                                "actor",
                                "optimizer",
                                "optimizer_sha256",
                                "optimizer_steps",
                                "group_ids",
                            )
                        },
                    },
                )
        else:
            generate_batch(
                config,
                deployment,
                read_credentials(deployment["credentials"]),
                request,
                args.output,
            )


if __name__ == "__main__":
    main()
