"""Static/evolve S2 orchestration; existing model, environment and training workers."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import fcntl
import re
import time
from pathlib import Path

from .demonstrations import sha256
from .io import digest, read_json, write_json
from .s2_inputs import load_bundle, prepare
from .s2_schedule import GroupFeedback, TaskQueue, select_contexts
from .s2_state import atomic, binding, evidence, unknown_episode
from .s2_reporting import best_round, evaluation_cost, paired, report, score
from .s2_storage import archive_requests, paired_inputs
from .v2_runtime import S2_RUNTIME, s2_execution, verify_lock
from .v2_tasks import ROOT, plan


def configuration():
    verify_lock(S2_RUNTIME)
    cfg = read_json(ROOT / f"configs/{S2_RUNTIME}.json")
    expected = read_json(ROOT / "configs/v2_s1_short_vllm_20261115.json")
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
        if cfg[key] != expected[key]:
            raise ValueError(
                "S2 changed the accepted model or shared evaluation protocol: " + key
            )
    old = read_json(ROOT / "configs/v2_s1_train100_20261113.json")
    if (
        cfg["feedback_task_ids"] != old["feedback_task_ids"]
        or len(cfg["feedback_task_ids"]) != 100
    ):
        raise ValueError("S2 must retain the existing train100")
    if cfg["test_task_ids"] != [
        r["task_id"] for r in plan()["rows"] if r["role"] == "test"
    ]:
        raise ValueError("S2 final test assignment changed")
    if any(
        cfg["s2"][k] != n
        for k, n in {
            "rounds": 5,
            "groups_per_round": 40,
            "group_size": 4,
            "groups_per_update": 2,
            "student_trajectories_per_branch": 800,
            "student_generated_tokens_per_branch": 800 * 16384,
            "student_requests_per_branch": 800 * 80,
        }.items()
    ):
        raise ValueError("Frozen S2 schedule or Student budget changed")
    if any(
        cfg["task_evolution"][k] != n
        for k, n in {
            "max_proposals": 192,
            "max_feedback_cycles": 24,
            "contexts_per_batch": 4,
            "explorations_per_context": 2,
            "proposals_per_context": 2,
        }.items()
    ):
        raise ValueError("Frozen S2 generation limits changed")
    return cfg


def evaluate(runtime, actor, tasks, output, *, final_test=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    policy = runtime.policy(actor)
    evidence(
        output / "evaluation_binding.json",
        {
            "policy": policy,
            "tasks": tasks,
            "final_test": final_test,
            "student_limits": runtime.cfg["student_limits"],
            "answer_format_contract": runtime.cfg["answer_format_contract"],
        },
    )
    for task in tasks:
        runtime.available(runtime.cfg["student_limits"]["wall_seconds"] + 120)
        ledger = output / (task + ".receipt.json")
        claim = output / (task + ".claim.json")
        directory = output / task
        if ledger.exists():
            result = read_json(ledger)
        elif (directory / "result.json").exists():
            result = {
                **read_json(directory / "result.json"),
                "directory": str(directory),
            }
            write_json(ledger, result)
        elif claim.exists():
            result = unknown_episode(
                {"task_id": task},
                policy,
                directory,
                "Interrupted evaluation; no repeat",
                42,
                runtime.cfg["student_limits"],
            )
            result["purpose"] = "evaluation"
            write_json(ledger, result)
        else:
            write_json(claim, {"policy": policy, "task_id": task})
            result = runtime.rollout(
                actor,
                {"task_id": task},
                directory,
                42,
                evaluation=True,
                final_test=final_test,
            )
            write_json(ledger, result)
        unstarted_unknown = (
            result["policy"] is None
            and result["success"] is None
            and result["classification"] == "infrastructure_error"
        )
        if (
            (result["policy"] != policy and not unstarted_unknown)
            or result["task_id"] != task
            or result.get("generated_task_id") is not None
        ):
            raise ValueError("Evaluation receipt identity mismatch")
        results.append(result)
    evidence(output / "results.json", results)
    for row in results:
        archive_requests(row["directory"])
    return results


class Experiment:
    def __init__(self, cfg, bundle, deployment, output, mode, runtime):
        if mode not in {"static", "evolve"}:
            raise ValueError("Unknown S2 experiment mode")
        self.cfg, self.bundle, self.deployment = cfg, bundle, deployment
        self.output, self.runtime = Path(output), runtime
        self.output.mkdir(parents=True, exist_ok=True)
        key = binding(
            cfg,
            {
                "pool": copy.deepcopy(bundle["pool"]),
                "archive_sha256": bundle["archive_sha256"],
                "s0": deployment["s0"],
                "mode": mode,
            },
        )
        path = self.output / "state.json"
        if path.exists():
            self.state = read_json(path)
            if self.state["binding"] != key:
                raise ValueError("S2 continuation changed experiment inputs")
        else:
            self.state = {
                "binding": key,
                "mode": mode,
                "actor": deployment["s0"],
                "optimizer": None,
                "optimizer_sha256": None,
                "optimizer_steps": 0,
                "groups": [],
                "updates": [],
                "updated_through": 0,
                "pending": None,
                "pool": copy.deepcopy(bundle["pool"]),
                "initial_ids": [t["task_id"] for t in bundle["pool"]],
                "lineage": {},
                "queue": None,
                "feedback_state": None,
                "events": [],
                "feedback_windows": {},
                "round_evaluations": {},
                "teacher_usage": {},
            }
            self.state["student_usage"] = {
                "model_requests": 0,
                "generated_tokens": 0,
                "unknown_generation_reservation": 0,
            }
            self.save()

    def save(self):
        atomic(self.output / "state.json", self.state)

    def group(self):
        index = len(self.state["groups"])
        round_index, slot = divmod(index, 40)
        pending = self.state["pending"]
        if pending is None:
            queue = TaskQueue(
                self.cfg["feedback_task_ids"],
                self.cfg["s2"]["seed"],
                self.state["queue"],
            )
            task = queue.take(
                round_index, slot, self.state["pool"], self.state["initial_ids"]
            )
            self.state["queue"] = queue.export()
            pending = {
                "index": index,
                "task": task,
                "policy": self.runtime.policy(self.state["actor"]),
            }
            self.state["pending"] = pending
            self.save()
        task, policy = pending["task"], pending["policy"]
        if self.runtime.policy(self.state["actor"]) != policy:
            raise ValueError("Pending group actor changed")
        directory = self.output / "groups" / f"{index:03d}"
        workers = s2_execution()["rollout_concurrency"]
        needs_rollout = any(
            not (directory / f"member_{member}.receipt.json").exists()
            and not (directory / f"member_{member}/result.json").exists()
            and not (directory / f"member_{member}.claim.json").exists()
            for member in range(4)
        )
        if workers > 1 and needs_rollout:
            self.runtime.prepare_rollouts(self.state["actor"])

        def member_rollout(member):
            self.runtime.available(self.cfg["student_limits"]["wall_seconds"] + 120)
            seed = self.cfg["s2"]["seed"] + index * 4 + member
            path = directory / f"member_{member}"
            receipt = directory / f"member_{member}.receipt.json"
            claim = directory / f"member_{member}.claim.json"
            if receipt.exists():
                result = read_json(receipt)
            elif (path / "result.json").exists():
                raw = read_json(path / "result.json")
                if s2_execution().get("completion_contract_revision"):
                    raw = self.runtime.finalize_reward(raw, path)
                result = {
                    **raw,
                    "directory": str(path),
                    "seed": seed,
                }
                write_json(receipt, result)
            elif claim.exists():
                result = unknown_episode(
                    task,
                    policy,
                    path,
                    "Interrupted rollout; no dispatched request replay",
                    seed,
                    self.cfg["student_limits"],
                )
                write_json(receipt, result)
            else:
                write_json(claim, {"task": task, "policy": policy, "seed": seed})
                result = self.runtime.rollout(self.state["actor"], task, path, seed)
                write_json(receipt, result)
            unstarted_unknown = (
                result["policy"] is None
                and result["success"] is None
                and result["classification"] == "infrastructure_error"
            )
            if (
                (result["policy"] != policy and not unstarted_unknown)
                or result["task_id"] != task["task_id"]
                or result["generated_task_id"]
                != task.get("generated_task", {}).get("task_id")
                or result["seed"] != seed
            ):
                raise ValueError("Training rollout identity changed")
            usage = result["usage"]
            if (
                usage.get("model_requests", 0)
                > self.cfg["student_limits"]["max_requests"]
                or usage.get("generated_tokens", 0)
                + usage.get("unknown_generation_reservation", 0)
                > self.cfg["student_limits"]["max_generated_tokens"]
            ):
                raise ValueError("Training rollout exceeded the per-episode budget")
            return result

        started = time.monotonic()
        if workers == 1:
            episodes = [member_rollout(member) for member in range(4)]
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                # map returns slot order, independent of completion order.
                episodes = list(executor.map(member_rollout, range(4)))
        ledger = GroupFeedback(
            self.cfg["task_evolution"]["signals"], self.state["feedback_state"]
        )
        event = ledger.observe(index, episodes)
        group = {"index": index, "task": task, "episodes": episodes, "event": event}
        group["execution"] = {
            **s2_execution(),
            "wall_seconds_this_invocation": time.monotonic() - started,
        }
        path = directory / "group.json"
        evidence(path, group)
        self.state["groups"].append(str(path))
        self.state["events"].append(event)
        self.state["feedback_state"] = ledger.state
        self.state["pending"] = None
        for episode in episodes:
            for key in self.state["student_usage"]:
                self.state["student_usage"][key] += episode["usage"].get(key, 0)
        used = self.state["student_usage"]
        if (
            used["model_requests"] > self.cfg["s2"]["student_requests_per_branch"]
            or used["generated_tokens"] + used["unknown_generation_reservation"]
            > self.cfg["s2"]["student_generated_tokens_per_branch"]
        ):
            raise ValueError("S2 branch Student budget exceeded")
        self.save()

    def update(self):
        end = len(self.state["groups"])
        if not end or end % 2 or self.state["updated_through"] == end:
            return
        groups = [read_json(p)["episodes"] for p in self.state["groups"][-2:]]
        directory = self.output / "updates" / f"{end // 2:03d}"
        receipt = directory / "committed.json"
        request = {
            "actor": self.state["actor"],
            "s0": self.deployment["s0"],
            "optimizer": self.state["optimizer"],
            "optimizer_sha256": self.state["optimizer_sha256"],
            "optimizer_steps": self.state["optimizer_steps"],
            "groups": groups,
            "group_ids": [end - 2, end - 1],
        }
        if receipt.exists():
            value = read_json(receipt)
            if value["request_sha256"] != digest(request):
                raise ValueError("Committed update does not match pending batch")
        else:
            value = {
                **self.runtime.update(request, directory),
                "request_sha256": digest(request),
                "groups_through": end,
            }
            write_json(receipt, value)
        if value["updated"]:
            self.state.update(
                actor=value["actor"],
                optimizer=value["optimizer"],
                optimizer_sha256=value["optimizer_sha256"],
                optimizer_steps=value["optimizer_steps"],
            )
        self.state["updated_through"] = end
        self.state["updates"].append(str(receipt))
        self.save()

    def cleanup(self):
        start, end = self.state.get("storage_through", 0), self.state["updated_through"]
        if start == end:
            return
        self.runtime.cleanup(
            [read_json(p)["episodes"] for p in self.state["groups"][start:end]],
            self.state,
            self.output,
        )
        self.state["storage_through"] = end
        self.save()

    def feedback(self, *, retry_window=None, retry_id=None):
        if s2_execution().get("background_generation") and retry_window is None:
            from .s2_background import collect, enqueue

            collect(self)
            enqueue(self)
            return
        end = len(self.state["groups"]) if retry_window is None else retry_window
        retry = retry_window is not None
        if retry and (
            self.state["mode"] != "evolve"
            or self.state["pending"] is not None
            or not isinstance(retry_id, str)
            or re.fullmatch(r"[a-z0-9_]{1,48}", retry_id) is None
            or str(end) not in self.state["feedback_windows"]
        ):
            raise ValueError(
                "Regeneration requires a completed evolve window and a new attempt ID"
            )
        if not retry and (
            not end
            or end % 8
            or end == 200
            or str(end) in self.state["feedback_windows"]
        ):
            return
        if self.state["mode"] == "static":
            return
        from .s2_evolution import GroupEvolution
        from .v2_round import isolated

        self.runtime.stop()
        if retry:
            original = read_json(self.state["feedback_windows"][str(end)])
            if original["candidate_count"] or original["accepted"]:
                raise ValueError(
                    "Only a zero-candidate failed window may be regenerated"
                )
            directory = (
                self.output / "generation" / f"window_{end:03d}_retry_{retry_id}"
            )
            events = original["selected"]
            if directory.name in self.state.get("feedback_regenerations", {}):
                return
            prior = [
                read_json(p)
                for p in self.state.get("feedback_regenerations", {}).values()
                if read_json(p)["completed_groups"] == end
            ]
            if any(p["candidate_count"] for p in prior):
                raise ValueError(
                    "This window already produced candidates in a regeneration"
                )
        else:
            directory = self.output / "generation" / f"window_{end:03d}"
            events = select_contexts(self.state["events"][-8:])
        evidence(
            directory / "selection.json",
            {"groups": list(range(end - 8, end)), "selected": events},
        )
        cost_key = (
            "teacher_usage_priced_" + self.cfg["teacher"]["pricing"]["currency"].lower()
        )
        summary = {cost_key: 0, "feedback": self.bundle["feedback"]}
        engine = GroupEvolution(
            self.cfg,
            self.cfg["models"][self.cfg["selected_model"]],
            {
                **self.deployment,
                "teacher_budget_amount": self.cfg["s2"]["teacher_budget"][
                    "priced_cost"
                ],
            },
            directory,
            summary,
            self.runtime.deadline,
            isolated,
            budget_root=self.output / "generation",
        )
        results = []
        for i, event in enumerate(events):
            self.runtime.available(120)
            engine.pool = copy.deepcopy(self.state["pool"])
            context_name = (
                f"s2_{digest(str(self.output.resolve()))[:12]}_w{end:03d}_c{i}"
            )
            if retry:
                context_name += "_retry_" + retry_id
            result = engine.run_context(event, directory / context_name)
            results.append(result)
            known = {t["task_id"] for t in self.state["pool"]}
            for task in result["accepted"]:
                if task["task_id"] not in known:
                    self.state["pool"].append(task)
                    self.state["lineage"][task["task_id"]] = {
                        **result["lineage"],
                        "round": (end - 1) // 40,
                    }
            self.state["teacher_usage"] = engine.usage()
            self.save()
        batch = {
            "completed_groups": end,
            "selected": events,
            "contexts": results,
            "selected_contexts": len(events),
            "actual_contexts": sum(r.get("context_completed", False) for r in results),
            "candidate_count": sum(r["candidate_count"] for r in results),
            "explorations": sum(r["explorations"] for r in results),
            "accepted": sum(len(r["accepted"]) for r in results),
            "teacher_usage_cumulative": engine.usage(),
            **(
                {
                    "retry_of_window": end,
                    "original_batch": self.state["feedback_windows"][str(end)],
                }
                if retry
                else {}
            ),
        }
        evidence(directory / "batch.json", batch)
        if retry:
            self.state.setdefault("feedback_regenerations", {})[directory.name] = str(
                directory / "batch.json"
            )
        else:
            self.state["feedback_windows"][str(end)] = str(directory / "batch.json")
        self.state["teacher_usage"] = engine.usage()
        self.save()

    def run(self, baseline):
        while True:
            self.update()
            self.cleanup()
            self.feedback()
            end = len(self.state["groups"])
            if end and end % 40 == 0:
                round_index = end // 40 - 1
                if str(round_index) not in self.state["round_evaluations"]:
                    target = self.output / "evaluation" / f"round_{round_index + 1}"
                    rows = evaluate(
                        self.runtime,
                        self.state["actor"],
                        self.cfg["dev_task_ids"],
                        target,
                    )
                    checks = paired_inputs(baseline, rows)
                    evidence(target / "paired_inputs.json", checks)
                    self.state["round_evaluations"][str(round_index)] = {
                        "round": round_index + 1,
                        "actor": self.state["actor"],
                        "optimizer": self.state["optimizer"],
                        "optimizer_sha256": self.state["optimizer_sha256"],
                        "optimizer_steps": self.state["optimizer_steps"],
                        "teacher_usage": copy.deepcopy(self.state["teacher_usage"]),
                        "trained_generated_through": self.state["queue"][
                            "generated_ever"
                        ].copy(),
                        "counts": score(rows),
                        "results": str(target / "results.json"),
                        "paired_vs_s0": paired(baseline, rows),
                    }
                    self.save()
                report(self.output, self.state, baseline)
            if end == 200:
                if (
                    s2_execution().get("background_generation")
                    and self.state["mode"] == "evolve"
                ):
                    atomic(
                        self.output / "generation/student_finished.json",
                        {"completed_groups": end},
                    )
                return self.state
            self.group()


def run(config, deployment, output, mode):
    from .s2_backend import BackendRuntime
    from .v2_s1_batch import checkpoint

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        bundle = load_bundle(deployment["initial_bundle"])
        checkpoint(deployment["s0"], config, initial=True)
        evidence(
            output / "experiment.json",
            {
                "config": config,
                "source_lock_sha256": sha256(ROOT / config["source_lock"]),
                "initial_pool_sha256": digest(bundle["pool"]),
                "s0": deployment["s0"],
            },
        )
        runtime = BackendRuntime(config, deployment, output / "gpu")
        evidence(
            output / "executions" / (s2_execution()["revision"] + ".json"),
            {
                "settings": s2_execution(),
                "source": verify_lock(S2_RUNTIME),
                "experiment_config_sha256": digest(config),
                "prior_source_lock_sha256": sha256(ROOT / config["source_lock"]),
                "deadline_epoch": deployment["deadline_epoch"],
            },
        )
        try:
            runtime.available(1320)
            baseline = evaluate(
                runtime, deployment["s0"], config["dev_task_ids"], output / "baseline"
            )
            for branch in ["static", "evolve"] if mode == "both" else [mode]:
                Experiment(
                    config, bundle, deployment, output / branch, branch, runtime
                ).run(baseline)
            if all(
                (output / b / "state.json").exists()
                and len(read_json(output / b / "state.json")["round_evaluations"]) == 5
                for b in ("static", "evolve")
            ):
                final_evaluation(config, deployment, output, runtime)
            atomic(
                output / "status.json",
                {
                    "status": "requested_modes_complete",
                    "mode": mode,
                    "updated_epoch": time.time(),
                },
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


def final_evaluation(config, deployment, output, runtime):
    output = Path(output)
    frozen = read_json(output / "experiment.json")
    if frozen["config"] != config or frozen["s0"] != deployment["s0"]:
        raise ValueError("Final evaluation differs from the trained experiment")
    selection = {
        b: best_round(read_json(output / b / "state.json")["round_evaluations"])
        for b in ("static", "evolve")
    }
    evidence(output / "final_selection.json", selection)
    results = {}
    baseline = None
    for label, actor in [
        ("S0", deployment["s0"]),
        *[(b, v["actor"]) for b, v in selection.items()],
    ]:
        rows = evaluate(
            runtime,
            actor,
            config["test_task_ids"],
            output / "test" / label,
            final_test=True,
        )
        results[label] = {
            "counts": score(rows),
            "cost": evaluation_cost(rows),
            "actor": actor,
            "results": str(output / "test" / label / "results.json"),
        }
        if baseline is None:
            baseline = rows
        else:
            evidence(
                output / "test" / label / "paired_inputs.json",
                paired_inputs(baseline, rows),
            )
            results[label]["paired_vs_s0"] = paired(baseline, rows)
    evidence(output / "test_summary.json", results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "prepare",
            "run",
            "resume",
            "evaluate",
            "report",
            "update-worker",
            "regenerate",
            "feedback-worker",
        ],
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--deployment", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--mode", choices=["static", "evolve", "both"], default="both")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--window", type=int)
    parser.add_argument("--attempt")
    args = parser.parse_args()
    if args.command == "update-worker" and args.input is None:
        parser.error("update-worker requires --input")
    if args.command != "update-worker" and args.output is None:
        parser.error("--output is required")
    if (
        args.command in {"run", "resume", "evaluate", "regenerate", "feedback-worker"}
        and args.deployment is None
    ):
        parser.error("--deployment is required")
    if args.command == "prepare" and args.archive is None:
        parser.error("prepare requires --archive")
    if args.command == "resume" and not (args.output / "experiment.json").exists():
        parser.error("resume requires an existing experiment output")
    if args.command == "regenerate" and (args.window is None or args.attempt is None):
        parser.error("regenerate requires --window and --attempt")
    if args.command == "update-worker":
        from .s2_update import update

        cfg = configuration()
        payload = read_json(args.input)
        if payload["config"] != cfg:
            raise ValueError("Training worker config differs from frozen S2")
        update(cfg, payload["request"], payload["output"])
        return
    cfg = configuration()
    if args.command == "feedback-worker":
        from .s2_background import worker

        if not s2_execution().get("background_generation"):
            raise ValueError(
                "Background Teacher requires its explicit execution revision"
            )
        deployment = read_json(args.deployment)
        worker(
            cfg,
            deployment,
            load_bundle(deployment["initial_bundle"]),
            args.output / "evolve",
        )
    elif args.command == "prepare":
        prepare(args.archive, args.output)
    elif args.command in {"run", "resume"}:
        run(cfg, read_json(args.deployment), args.output, args.mode)
    elif args.command == "regenerate":
        from .s2_backend import BackendRuntime

        if s2_execution().get("background_generation"):
            from .s2_background import enqueue_regeneration

            deployment = read_json(args.deployment)
            runtime = BackendRuntime(cfg, deployment, args.output / "gpu")
            with (args.output / "controller.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                experiment = Experiment(
                    cfg,
                    load_bundle(deployment["initial_bundle"]),
                    deployment,
                    args.output / "evolve",
                    "evolve",
                    runtime,
                )
                enqueue_regeneration(experiment, args.window, args.attempt)
            return
        deployment = read_json(args.deployment)
        runtime = BackendRuntime(cfg, deployment, args.output / "gpu")
        with (args.output / "controller.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            runtime.available(1320)
            evidence(
                args.output / "executions" / (s2_execution()["revision"] + ".json"),
                {
                    "settings": s2_execution(),
                    "source": verify_lock(S2_RUNTIME),
                    "experiment_config_sha256": digest(cfg),
                    "prior_source_lock_sha256": sha256(ROOT / cfg["source_lock"]),
                    "deadline_epoch": deployment["deadline_epoch"],
                },
            )
            experiment = Experiment(
                cfg,
                load_bundle(deployment["initial_bundle"]),
                deployment,
                args.output / "evolve",
                "evolve",
                runtime,
            )
            try:
                experiment.feedback(retry_window=args.window, retry_id=args.attempt)
            finally:
                runtime.stop()
    elif args.command == "report":
        baseline = read_json(args.output / "baseline/results.json")
        for branch in ("static", "evolve"):
            path = args.output / branch / "state.json"
            if path.exists():
                report(path.parent, read_json(path), baseline)
    else:
        from .s2_backend import BackendRuntime

        deployment = read_json(args.deployment)
        runtime = BackendRuntime(cfg, deployment, args.output / "gpu")
        with (args.output / "controller.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                final_evaluation(cfg, deployment, args.output, runtime)
            finally:
                runtime.stop()


if __name__ == "__main__":
    main()
