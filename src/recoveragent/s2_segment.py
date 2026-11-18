"""Bounded continuation of an accepted Evolve actor, reusing the S2 runner."""

import copy
import fcntl
import subprocess
import time
from pathlib import Path

from .demonstrations import sha256
from .io import digest, read_json, write_json
from .s2_experiment import Experiment, evaluate, require_pool_protocol
from .s2_state import atomic, evidence, protocol_identity
from .v2_grpo import group_advantages


def update_request(state, deployment, *, tail=False):
    ids = state["segment"]["pending_effective"][:2]
    if len(ids) < 2 and not (tail and len(ids) == 1):
        return None
    return {
        **{
            k: state[k]
            for k in ("actor", "optimizer", "optimizer_sha256", "optimizer_steps")
        },
        "s0": deployment["s0"],
        "group_ids": ids,
        "groups": [read_json(state["groups"][i])["episodes"] for i in ids],
        "segment_parent_updates": state["segment"]["parent_updates"],
        "audit_behavior": not state["segment"].get("behavior_audit_passed", False),
    }


class Segment(Experiment):
    def __init__(self, cfg, bundle, deployment, output, mode, runtime):
        super().__init__(cfg, bundle, deployment, output, mode, runtime)
        if "segment" in self.state:
            return
        spec = deployment["segment_parent"]
        if sha256(spec["state"]) != spec["state_sha256"]:
            raise ValueError("Parent state changed")
        parent = read_json(spec["state"])
        for key, value in protocol_identity().items():
            if key != "execution" and parent["protocols"].get(key) != value:
                raise ValueError(
                    "Parent public execution/reward protocol differs: " + key
                )
        if (
            len(parent["groups"]) != cfg["segment"]["parent_groups"]
            or parent["optimizer_steps"] != cfg["segment"]["parent_updates"]
            or parent["pending"] is not None
            or parent["optimizer_sha256"] != spec["optimizer_sha256"]
        ):
            raise ValueError("Parent is not the frozen completed round")
        for key in (
            "actor",
            "optimizer",
            "optimizer_sha256",
            "optimizer_steps",
            "groups",
            "events",
            "pool",
            "initial_ids",
            "lineage",
            "queue",
            "feedback_state",
            "feedback_windows",
        ):
            self.state[key] = copy.deepcopy(parent[key])
        end = len(parent["groups"])
        self.state.update(updated_through=end, storage_through=end)
        self.state["segment"] = {
            "parent": spec,
            "parent_groups": end,
            "parent_updates": parent["optimizer_steps"],
            "parent_actor": parent["actor"],
            "parent_optimizer": parent["optimizer"],
            "parent_pool_ids": [t["task_id"] for t in parent["pool"]],
            "parent_teacher_usage": parent.get("teacher_usage", {}),
            "scanned_through": end,
            "pending_effective": [],
            "consumed_groups": [],
        }
        self.save()

    def collect_effective(self):
        segment = self.state["segment"]
        for i in range(segment["scanned_through"], len(self.state["groups"])):
            group = read_json(self.state["groups"][i])
            if group["event"]["complete"]:
                advantages = group_advantages(group["episodes"])
                if advantages and any(advantages):
                    if any(
                        e["policy"] != self.runtime.policy(self.state["actor"])
                        for e in group["episodes"]
                    ):
                        raise ValueError("Effective batch contains an old actor")
                    segment["pending_effective"].append(i)
            segment["scanned_through"] = i + 1
        # Unconsumed requests must remain available to the training worker.
        self.state["updated_through"] = min(
            segment["pending_effective"], default=segment["scanned_through"]
        )
        self.save()

    def update(self, *, tail=False):
        self.collect_effective()
        segment = self.state["segment"]
        if (
            self.state["optimizer_steps"] - segment["parent_updates"]
            >= self.cfg["segment"]["max_new_updates"]
        ):
            return
        request = update_request(self.state, self.deployment, tail=tail)
        if request is None:
            return
        directory = self.output / "updates" / f"{len(self.state['updates']) + 1:03d}"
        receipt = directory / "committed.json"
        if receipt.exists():
            value = read_json(receipt)
            if value["request_sha256"] != digest(request):
                raise ValueError("Committed update differs from effective batch")
        else:
            value = {
                **self.runtime.update(request, directory),
                "request_sha256": digest(request),
                "group_ids": request["group_ids"],
                "effective_group_count": len(request["group_ids"]),
                "groups_through": len(self.state["groups"]),
                "tail_batch": len(request["group_ids"]) == 1,
            }
            write_json(receipt, value)
        if value["updated"]:
            if value["optimizer_steps"] != self.state["optimizer_steps"] + 1:
                raise ValueError("Optimizer did not advance exactly once")
            self.state.update(
                {
                    k: value[k]
                    for k in (
                        "actor",
                        "optimizer",
                        "optimizer_sha256",
                        "optimizer_steps",
                    )
                }
            )
            segment["behavior_audit_passed"] = True
        segment["consumed_groups"].extend(request["group_ids"])
        segment["pending_effective"] = segment["pending_effective"][
            len(request["group_ids"]) :
        ]
        self.state["updates"].append(str(receipt))
        self.state["updated_through"] = min(
            segment["pending_effective"], default=segment["scanned_through"]
        )
        self.save()

    def stop_reason(self):
        s, limits = self.state["segment"], self.cfg["segment"]
        if (
            self.state["optimizer_steps"] - s["parent_updates"]
            >= limits["max_new_updates"]
        ):
            return "new_update_limit"
        if len(self.state["groups"]) - s["parent_groups"] >= limits["max_new_groups"]:
            return "new_group_limit"
        return None

    def train_segment(self):
        s = self.state["segment"]
        if s.get("training_finished_reason"):
            return
        try:
            while True:
                self.update()
                self.cleanup()
                reason = self.stop_reason()
                if reason:
                    self.update(tail=True)
                    break
                self.feedback()
                self.group()
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            reason = "training_time_limit"
            s["time_limit_detail"] = str(exc)
            self.collect_effective()
            # A single tail is permitted only while time/update budgets remain.
            if len(s["pending_effective"]) == 1:
                try:
                    self.runtime.available(300)
                    self.update(tail=True)
                except (TimeoutError, subprocess.TimeoutExpired):
                    pass
        s["training_finished_reason"] = reason
        self.save()
        atomic(
            self.output / "generation/student_finished.json",
            {"completed_groups": len(self.state["groups"]), "reason": reason},
        )
        from .s2_background import collect

        collect(self)
        self.cleanup()
        self.summarize()

    def summarize(self):
        from .reward_validity import valid_reward

        s = self.state["segment"]
        counts = {}
        for ref in self.state["groups"][s["parent_groups"] :]:
            g = read_json(ref)
            generated = g["episodes"][0]["generated_task_id"]
            source = (
                "original"
                if generated is None
                else "initial_generated"
                if generated in self.state["initial_ids"]
                else "new_generated"
            )
            c = counts.setdefault(
                source,
                {
                    "groups": 0,
                    "success": 0,
                    "failure": 0,
                    "invalid": 0,
                    "varying_groups": 0,
                    "updated_groups": 0,
                },
            )
            c["groups"] += 1
            for e in g["episodes"]:
                c[
                    "invalid"
                    if not valid_reward(e)
                    else "success"
                    if e["success"]
                    else "failure"
                ] += 1
            c["varying_groups"] += int(
                g["event"]["complete"] and any(group_advantages(g["episodes"]) or [])
            )
            c["updated_groups"] += int(
                any(
                    read_json(p)["updated"] and g["index"] in read_json(p)["group_ids"]
                    for p in self.state["updates"]
                )
            )
        value = {
            "parent_updates": s["parent_updates"],
            "new_updates": self.state["optimizer_steps"] - s["parent_updates"],
            "cumulative_updates": self.state["optimizer_steps"],
            "new_groups": len(self.state["groups"]) - s["parent_groups"],
            "all_attempts_before_filtering": counts,
            "pending_effective": s["pending_effective"],
            "student_usage": self.state["student_usage"],
            "teacher_usage": self.state["teacher_usage"],
            "stop_reason": s.get("training_finished_reason"),
            "actor": self.state["actor"],
            "optimizer": self.state["optimizer"],
            "committed_updates": [read_json(p) for p in self.state["updates"]],
        }
        atomic(self.output / "segment_summary.json", value)
        return value


def reuse_baseline(experiment, seed):
    source = experiment.deployment.get("reuse_s0_baseline")
    if source is None or seed != 42:
        return None
    source = Path(source)
    bound = read_json(source / "evaluation_binding.json")
    old = read_json(source.parent / "experiment.json")["config"]
    cfg = experiment.cfg
    for key in (
        "models",
        "selected_model",
        "s0",
        "lora",
        "student_limits",
        "answer_format_contract",
        "inference",
        "dev_task_ids",
    ):
        if old[key] != cfg[key]:
            raise ValueError("Baseline comparison setting differs: " + key)
    for key, value in protocol_identity().items():
        if key != "execution" and bound["protocols"].get(key) != value:
            raise ValueError("Baseline public protocol differs: " + key)
    rows = read_json(source / "results.json")
    if (
        [e["task_id"] for e in rows] != cfg["dev_task_ids"]
        or bound["policy"] != experiment.runtime.policy(experiment.deployment["s0"])
        or any(e["seed"] != seed or e["policy"] != bound["policy"] for e in rows)
    ):
        raise ValueError("Baseline actor, order or seed differs")
    evidence(
        experiment.output / "paired_dev/reused_s0_seed42.json",
        {
            "source": str(source),
            "results_sha256": sha256(source / "results.json"),
            "original_binding": bound,
            "current_protocols": protocol_identity(),
            "reuse_only": True,
            "reason": "Same existing L40S, S0, vLLM, seed and public evaluation protocol; only training/generation execution changed",
        },
    )
    return rows


def paired_evaluation(experiment):
    """Four frozen seeds; completed receipts survive the absolute deadline."""
    cfg, runtime = experiment.cfg, experiment.runtime
    all_rows = {"s0": [], "final": []}
    incomplete = None
    for seed in cfg["segment"]["evaluation_seeds"]:
        for label, actor in (
            ("s0", experiment.deployment["s0"]),
            ("final", experiment.state["actor"]),
        ):
            target = experiment.output / "paired_dev" / f"seed_{seed}" / label
            try:
                rows = reuse_baseline(experiment, seed) if label == "s0" else None
                if rows is None:
                    rows = evaluate(
                        runtime, actor, cfg["dev_task_ids"], target, seed=seed
                    )
            except TimeoutError as exc:
                rows = [
                    read_json(target / (t + ".receipt.json"))
                    for t in cfg["dev_task_ids"]
                    if (target / (t + ".receipt.json")).exists()
                ]
                incomplete = str(exc)
            all_rows[label].extend(rows)
            if label == "final" and len(rows) == 24:
                from .s2_storage import paired_inputs

                original = [e for e in all_rows["s0"] if e["seed"] == seed]
                evidence(target / "paired_inputs.json", paired_inputs(original, rows))
            if incomplete:
                break
        if incomplete:
            break
    result = {
        "seeds": cfg["segment"]["evaluation_seeds"],
        "planned_attempts_per_model": 96,
        "incomplete_reason": incomplete,
        "models": {},
        "per_task": [],
    }
    for label, rows in all_rows.items():
        successes = sum(e["success"] is True for e in rows)
        result["models"][label] = {
            "completed": len(rows),
            "success": successes,
            "failure": sum(e["success"] is False for e in rows),
            "unknown": sum(e["success"] is None for e in rows),
            "missing": 96 - len(rows),
            "reused_attempts": sum(e["seed"] == 42 for e in rows)
            if label == "s0" and experiment.deployment.get("reuse_s0_baseline")
            else 0,
            "mean_single_attempt_success_rate": successes / len(rows) if rows else None,
            "model_requests": sum(e["usage"].get("model_requests", 0) for e in rows),
            "generated_tokens": sum(
                e["usage"].get("generated_tokens", 0) for e in rows
            ),
            "wall_seconds": sum(e.get("wall_seconds", 0) for e in rows),
        }
    for task in cfg["dev_task_ids"]:
        values = {
            label: {e["seed"]: e["success"] for e in rows if e["task_id"] == task}
            for label, rows in all_rows.items()
        }
        paired = set(values["s0"]) & set(values["final"])
        result["per_task"].append(
            {
                "task_id": task,
                "outcomes": values,
                "paired_seeds": sorted(paired),
                "paired_success_count_delta": sum(
                    (values["final"][s] is True) - (values["s0"][s] is True)
                    for s in paired
                    if values["s0"][s] is not None and values["final"][s] is not None
                ),
            }
        )
    atomic(experiment.output / "paired_dev_summary.json", result)
    return result


def run_segment(config, deployment, output, mode):
    from .s2_backend import BackendRuntime
    from .s2_inputs import load_bundle
    from .v2_runtime import S2_RUNTIME, verify_lock
    from .v2_training import verify_checkpoint

    if mode != "evolve":
        raise ValueError("This bounded continuation authorizes Evolve only")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        bundle = load_bundle(deployment["initial_bundle"])
        require_pool_protocol(bundle)
        evidence(
            output / "experiment.json",
            {
                "config": config,
                "protocols": protocol_identity(),
                "source": verify_lock(S2_RUNTIME),
                "s0": deployment["s0"],
                "parent": deployment["segment_parent"],
                "deadline_epoch": deployment["deadline_epoch"],
                "training_deadline_epoch": deployment["training_deadline_epoch"],
            },
        )
        runtime = BackendRuntime(config, deployment, output / "gpu")
        try:
            identity = config["models"][config["selected_model"]]
            if (
                verify_checkpoint(deployment["s0"], identity)["adapter_sha256_after"]
                != config["s0"]["adapter_sha256"]
            ):
                raise ValueError("Common S0 changed")
            experiment = Segment(
                config, bundle, deployment, output / mode, mode, runtime
            )
            verify_checkpoint(experiment.state["actor"], identity)
            if (
                sha256(experiment.state["optimizer"])
                != experiment.state["optimizer_sha256"]
            ):
                raise ValueError("Continuation optimizer changed")
            deadline = runtime.deadline
            if deployment["training_deadline_epoch"] > deployment["deadline_epoch"]:
                raise ValueError("Training cannot extend authorization")
            runtime.deadline = min(
                deadline,
                time.monotonic()
                + max(0, deployment["training_deadline_epoch"] - time.time()),
            )
            experiment.train_segment()
            runtime.deadline = deadline
            paired_evaluation(experiment)
            from .s2_background import collect

            collect(experiment)
            experiment.summarize()
            atomic(
                output / "status.json",
                {"status": "bounded_segment_finished", "updated_epoch": time.time()},
            )
        except Exception as exc:
            atomic(
                output / "status.json",
                {
                    "status": "stopped",
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                    "updated_epoch": time.time(),
                },
            )
            raise
        finally:
            runtime.stop()
