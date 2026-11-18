"""Thin GPU phase switching over existing vLLM inference and GRPO workers."""

import copy
from pathlib import Path
import subprocess
import time

from .io import digest, read_json, write_json
from .s2_state import atomic, evidence, unknown_episode
from .v2_inference import episode
from .v2_model import load_tokenizer
from .v2_training import verify_checkpoint
from .v2_vllm import Backend
from .v2_runtime import s2_execution
from .reward_validity import valid_reward


def cuda_oom(text):
    value = str(text).lower()
    return (
        "cuda out of memory" in value
        or "torch.outofmemoryerror" in value
        or "torch.cuda.outofmemoryerror" in value
    )


def oom_update_result(request, log_path):
    return {
        "updated": False,
        "reason": "cuda_oom_skipped_update",
        "actor": request["actor"],
        "optimizer": request.get("optimizer"),
        "optimizer_sha256": request.get("optimizer_sha256"),
        "optimizer_steps": request["optimizer_steps"],
        "group_ids": request["group_ids"],
        "error_log": str(log_path),
    }


class BackendRuntime:
    def __init__(self, config, deployment, output):
        self.cfg, self.deployment, self.output = config, deployment, Path(output)
        self.identity = config["models"][config["selected_model"]]
        self.tokenizer = None
        self.server, self.actor, self.backend = None, None, None
        if type(deployment.get("deadline_epoch")) not in (int, float):
            raise ValueError(
                "Set deadline_epoch to the explicitly authorized resource deadline; no implicit extension"
            )
        self.deadline = time.monotonic() + max(
            0, deployment["deadline_epoch"] - time.time()
        )

    def available(self, reserve=0):
        if time.monotonic() + reserve >= self.deadline:
            raise TimeoutError(
                "Authorized instance deadline reached; resume with an authorized deployment"
            )

    def stop(self):
        import psutil

        receipt = self.output / "active_service.json"
        if receipt.exists():
            old = read_json(receipt)
            try:
                process = psutil.Process(old["pid"])
                if (
                    process.create_time() == old["create_time"]
                    and process.cmdline() == old["command"]
                ):
                    children = process.children(recursive=True)
                    process.terminate()
                    for child in children:
                        try:
                            child.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    _, alive = psutil.wait_procs([process, *children], timeout=20)
                    for item in alive:
                        item.kill()
                    psutil.wait_procs(alive, timeout=10)
            except psutil.NoSuchProcess:
                pass
        if self.server is not None:
            self.server.wait(timeout=30)
        self.server = self.actor = self.backend = None

    def serve(self, actor):
        if (
            self.actor == str(actor)
            and self.server is not None
            and self.server.poll() is None
        ):
            return self.backend
        self.available(180)
        self.stop()
        hardware = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=uuid,name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            text=True,
        ).strip()
        evidence(self.output / "hardware.json", {"gpu": hardware})
        import psutil
        import requests
        from scripts.probe_v2_vllm import probe

        directory = self.output / "services" / str(time.time_ns())
        directory.mkdir(parents=True)
        d = copy.deepcopy(self.deployment)
        d.update(
            s1=str(actor),
            vllm_pidfile=str(directory / "server.pid"),
            vllm_url="http://127.0.0.1:18000",
            vllm_models={
                "s1": {
                    "name": "S2_actor",
                    "receipt": str(directory / "binding.json"),
                }
            },
        )
        command = [
            d["vllm_python"],
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            d["model_snapshot"],
            "--served-model-name",
            "Qwen3.5-9B-pinned",
            "--host",
            "127.0.0.1",
            "--port",
            "18000",
            "--dtype",
            "bfloat16",
            "--max-model-len",
            "65536",
            "--max-num-seqs",
            "2",
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
            "2",
            "--lora-target-modules",
            "qkv_proj",
            "o_proj",
            "gate_up_proj",
            "down_proj",
            "--lora-modules",
            "S2_actor=" + str(actor),
            "--logprobs-mode",
            "processed_logprobs",
            "--limit-mm-per-prompt",
            '{"image":0,"video":0}',
            "--generation-config",
            "vllm",
            "--no-enable-log-requests",
        ]
        with (directory / "server.log").open("x") as log:
            self.server = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
        atomic(
            self.output / "active_service.json",
            {
                "pid": self.server.pid,
                "create_time": psutil.Process(self.server.pid).create_time(),
                "command": command,
            },
        )
        Path(d["vllm_pidfile"]).write_text(str(self.server.pid))
        http = requests.Session()
        http.trust_env = False
        until = min(self.deadline, time.monotonic() + 600)
        while time.monotonic() < until:
            if self.server.poll() is not None:
                raise RuntimeError("vLLM exited; inspect private server.log")
            try:
                if http.get(d["vllm_url"] + "/health", timeout=5).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(2)
        else:
            raise TimeoutError("vLLM startup timed out")
        first = not (self.output / "full_window_probe.json").exists()
        proof = probe(
            d, directory / "probe", cfg=self.cfg, checkpoint_key="s1", full_window=first
        )
        if first:
            write_json(
                self.output / "full_window_probe.json",
                {"directory": str(directory), "policy": proof["policy"]},
            )
        self.backend = Backend(
            d["vllm_url"],
            d["vllm_models"]["s1"]["receipt"],
            proof["policy"],
            "S2_actor",
        )
        self.actor = str(actor)
        return self.backend

    def policy(self, actor):
        saved = verify_checkpoint(actor, self.identity)
        return {
            **self.identity,
            "checkpoint_id": digest({"adapter_sha256": saved["adapter_sha256_after"]}),
        }

    def cleanup(self, groups, state, output):
        from .s2_storage import archive_requests, retain_committed

        for group in groups:
            for row in group:
                archive_requests(row["directory"])
        retain_committed(state, output)

    def prepare_rollouts(self, actor):
        # Start the shared service/tokenizer on the controller thread before workers.
        # No actor switch is allowed until the whole group has returned.
        if getattr(self, "restart_after_group", False):
            self.stop()
            self.restart_after_group = False
        self.serve(actor)
        if self.tokenizer is None:
            self.tokenizer = load_tokenizer(self.identity)

    def rollout(
        self, actor, task, directory, seed, *, evaluation=False, final_test=False
    ):
        self.available(self.cfg["student_limits"]["wall_seconds"] + 120)
        directory = Path(directory)
        policy = self.policy(actor)
        backend = self.serve(actor)
        if self.tokenizer is None:
            self.tokenizer = load_tokenizer(self.identity)
        request = {
            "model": self.identity,
            "checkpoint_id": policy["checkpoint_id"],
            "student_limits": {**self.cfg["student_limits"], "seed": seed},
            "task_id": task["task_id"],
            "generated_task": task.get("generated_task"),
            "appworld_root": self.deployment["appworld_root"],
            "output": str(directory),
            "purpose": "evaluation" if evaluation else "grpo",
            "final_test": final_test,
            "environment_contract": self.cfg["environment_contract"],
            "execution_protocol": self.cfg["execution_protocol"],
            "answer_format_contract": self.cfg["answer_format_contract"],
            "generated_scoring_revision": s2_execution().get(
                "generated_scoring_revision"
            ),
        }
        if s2_execution().get("completion_contract_revision"):
            request["completion_contract_revision"] = s2_execution()[
                "completion_contract_revision"
            ]
        from .tool_discovery import KEY as DISCOVERY_KEY

        if s2_execution().get(DISCOVERY_KEY):
            request[DISCOVERY_KEY] = s2_execution()[DISCOVERY_KEY]
        if task.get("diagnostic_goal_probe"):
            request["purpose"] = "protocol_probe"
        # v2_episode expects an omitted generated_task for original tasks.
        if request["generated_task"] is None:
            request.pop("generated_task")
        try:
            result = episode(
                request,
                None,
                self.tokenizer,
                python=self.deployment["worker_python"],
                deadline=self.deadline,
                generate_fn=backend,
            )
        except Exception as exc:
            if (directory / "result.json").exists():
                result = read_json(directory / "result.json")
            else:
                result = unknown_episode(
                    task, policy, directory, str(exc), seed, self.cfg["student_limits"]
                )
        result = self.finalize_reward(result, directory)
        if s2_execution().get("skip_cuda_oom") and (
            cuda_oom(result.get("fault", {}))
            or (directory / "backend_failure.json").exists()
        ):
            # Threads finish the current group before the controller restarts
            # vLLM. Already attempted trajectories remain unknown, not replayed.
            self.restart_after_group = True
        return {**result, "directory": str(directory), "seed": seed}

    def finalize_reward(self, result, directory):
        from .s2_state import protocol_identity

        path = Path(directory) / "reward_result.json"
        if path.exists():
            saved = read_json(path)
            if saved.get("protocols") != protocol_identity():
                raise ValueError("Reward receipt protocol mismatch")
            if saved["raw_result_sha256"] != digest(result):
                raise ValueError("Reward receipt does not match raw episode")
            return saved["result"]
        if (result.get("evaluation") or {}).get("status") not in {
            "needs_semantic",
            "needs_llm_judge",
        } or result.get("fault"):
            return result
        from .s2_reward import score

        judged = score(self, result, directory)
        write_json(
            path,
            {
                "raw_result_sha256": digest(result),
                "protocols": protocol_identity(),
                "result": judged,
            },
        )
        return judged

    def update(self, request, directory):
        from .v2_grpo import group_advantages

        usable = [g for g in request["groups"] if all(valid_reward(e) for e in g)]
        if not any(any(group_advantages(g) or []) for g in usable):
            return {
                "updated": False,
                "reason": "no_scorable_reward_variation",
                "actor": request["actor"],
                "optimizer": request.get("optimizer"),
                "optimizer_steps": request["optimizer_steps"],
            }
        self.stop()
        self.available(300)
        directory = Path(directory)
        # A failed/uncommitted update gets a new output directory on resume.
        directory.mkdir(parents=True, exist_ok=True)
        for path in sorted(directory.glob("attempt_*/result.json")):
            saved = read_json(directory / (path.parent.name + ".json"))
            if saved["request"] != request or saved["config"] != self.cfg:
                raise ValueError("Uncommitted update result has different inputs")
            return read_json(path)
        attempt = directory / ("attempt_" + str(time.time_ns()))
        payload = directory / (attempt.name + ".json")
        write_json(
            payload, {"config": self.cfg, "request": request, "output": str(attempt)}
        )
        started = time.monotonic()
        try:
            with (directory / (attempt.name + ".log")).open("x") as log:
                subprocess.run(
                    [
                        self.deployment["training_python"],
                        "-m",
                        "recoveragent.s2_experiment",
                        "update-worker",
                        "--input",
                        str(payload),
                    ],
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=max(1, self.deadline - time.monotonic()),
                )
        except subprocess.CalledProcessError:
            log_path = directory / (attempt.name + ".log")
            if not s2_execution().get("skip_cuda_oom") or not cuda_oom(
                log_path.read_text()
            ):
                raise
            # The worker owns a separate model/optimizer. Never adopt partial
            # files from its failed update; retain the last verified parent.
            write_json(attempt / "result.json", oom_update_result(request, log_path))
        finally:
            write_json(
                directory / (attempt.name + ".timing.json"),
                {
                    "wall_seconds": time.monotonic() - started,
                    "result_saved": (attempt / "result.json").exists(),
                },
            )
        return read_json(attempt / "result.json")
