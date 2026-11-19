"""CodeAct exploration/Solver with the existing lightweight generator and shared Judge."""

import copy
import fcntl
import json
import time
import uuid
from pathlib import Path

from recoveragent.coevolve_light import (
    duplicate_of,
    public_instruction,
    same_source_tasks,
)
from recoveragent.coevolve_llm import (
    candidate,
    admit,
    judge,
    pending,
    finish,
    JudgeError,
    evidence,
)
from recoveragent.coevolve_prompts import GENERATION, EXPLORATION, JUDGE
from recoveragent.coevolve_tasks import identify
from recoveragent.coevolve_generation import json_object
from recoveragent.io import digest, read_json, write_json
from recoveragent.teacher import (
    TeacherPolicy,
    normalize_message,
    response_usage,
    response_model_matches,
    usage_cost,
)
from recoveragent.teacher_context import receipt
from recoveragent.v2_tasks import context_for
from .client import Teacher
from .collect import replace_json
from .episode import run
from .protocol import identity, now


def epoch():
    return time.clock_gettime(time.CLOCK_REALTIME)


class Budget:
    """Shared finite reservations for background generation and foreground reward calls."""

    def __init__(self, directory, config, deadline):
        self.root, self.config, self.deadline = Path(directory), config, deadline
        self.root.mkdir(parents=True, exist_ok=True)

    def call(self, payload, transport, role):
        reserved = {
            "requests": 1,
            "generated_tokens": payload["max_tokens"],
            "prompt_tokens": self.config["teacher"]["billing_context_tokens"],
        }
        reserved["priced_cost"] = usage_cost(
            {**reserved, "cached_prompt_tokens": 0}, self.config["teacher"]["pricing"]
        )
        path = self.root / (uuid.uuid4().hex + ".json")
        with (self.root / "budget.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            total = {k: 0 for k in reserved}
            for p in self.root.glob("*.json"):
                d = read_json(p)["usage"]
                for k in total:
                    total[k] += d[k]
            if epoch() >= self.deadline or any(
                total[k] + reserved[k] > self.config["online"]["teacher_budget"][k]
                for k in total
            ):
                raise JudgeError("Shared Teacher budget/deadline exhausted")
            write_json(
                path,
                {"role": role, "usage": reserved, "known": False, "started": epoch()},
            )
        started = now()
        response = transport(
            payload
        )  # Failures retain the reservation, never become reward zero.
        usage = response_usage(response)
        replace_json(
            path,
            {
                "role": role,
                "known": True,
                "seconds": now() - started,
                "usage": {
                    "requests": 1,
                    "generated_tokens": usage["generated_tokens"],
                    "prompt_tokens": usage["prompt_tokens"],
                    "priced_cost": usage_cost(usage, self.config["teacher"]["pricing"]),
                },
                "provider_usage": response.get("usage", {}),
            },
        )
        return response


class Engine:
    def __init__(self, config, deployment, credentials):
        self.config, self.deployment, self.credentials = config, deployment, credentials
        if config["online"]["judge_text"]["prompt_sha256"] != digest(JUDGE):
            raise JudgeError("Frozen shared Judge prompt differs")
        self.judge_identity = {
            "role": "judge",
            **config["online"]["judge_text"],
            "protocol": identity(),
        }
        self.budget = Budget(
            deployment["teacher_ledger"], config, deployment["deadline"]
        )

    def text(self, source, directory, system, content, *, role=None):
        context_for(source, training=True)
        directory = Path(directory)
        if (directory / "response.json").exists():
            response = read_json(directory / "response.json")
            request = read_json(directory / "request.json")
        else:
            cfg = copy.deepcopy(self.config)
            cfg["teacher"]["judge_text"] = cfg["online"]["judge_text"]
            policy = TeacherPolicy(
                {
                    "teacher": cfg["teacher"],
                    "authorization": cfg["authorization"],
                    "tool_mode": "appworld_code",
                },
                self.credentials,
            )
            request = policy.build_text_request(
                [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(content, ensure_ascii=False),
                    },
                ],
                4096,
                role=role,
            )
            audit = receipt(request, cfg["teacher_context"])
            evidence(directory / "context.json", audit)
            if not audit["within_budget"]:
                raise JudgeError("Teacher context window exceeded; no truncation")
            evidence(directory / "request.json", request)
            response = self.budget.call(request, policy.transport, role or "generation")
            write_json(directory / "response.json", response)
        if not response_model_matches(self.config["teacher"], response.get("model")):
            raise JudgeError("Teacher returned a different model")
        message = normalize_message(
            response, allow_reasoning=request.get("thinking") == {"type": "enabled"}
        )
        return json_object(message["content"])

    def execute(self, source, directory, exploration):
        cfg = copy.deepcopy(self.config)
        teacher = Teacher(cfg, self.credentials)
        transport = teacher.policy.transport
        teacher.policy.transport = lambda payload: self.budget.call(
            payload,
            transport,
            "solver" if exploration["mode"] == "validation" else "exploration",
        )
        task = exploration.get("task")
        instruction = (
            None
            if task
            else EXPLORATION
            + "\n"
            + json.dumps(exploration["context"], ensure_ascii=False)
        )
        run(
            source,
            1,
            directory,
            self.deployment["appworld_root"],
            cfg,
            teacher,
            generated_task=task,
            instruction=instruction,
        )
        return read_json(Path(directory) / "trajectory.json")


def reward(engine, task, trajectory, directory):
    if trajectory.get("fault"):
        return {
            "success": None,
            "reward_valid": False,
            "reason": trajectory["fault"],
            "status": "infrastructure_error",
        }
    evaluation = pending(
        task,
        trajectory["messages"],
        trajectory["coevolve_terminal"],
        initial_state_hash=trajectory["initial_state_hash"],
    )
    try:
        result = finish(
            evaluation,
            judge(
                engine,
                task["source_task_id"],
                evaluation,
                directory,
                purpose="student_reward",
            ),
        )
    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
        result = {
            **evaluation,
            "success": None,
            "reward_valid": False,
            "reason": str(exc),
            "status": "scoring_gap",
        }
    write_json(Path(directory) / "result.json", result)
    return result


def generate_batch(config, deployment, credentials, request, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    engine = Engine(config, deployment, credentials)
    pool, attempts = copy.deepcopy(request["pool"]), []
    for index, event in enumerate(request["contexts"]):
        target = directory / f"context_{index:02d}"
        if (target / "result.json").exists():
            done = read_json(target / "result.json")
            pool.extend(done["accepted"])
            attempts.extend(done["attempts"])
            continue
        accepted, context_attempts = [], []
        try:
            if not event["complete"] or len(event["episodes"]) != 4:
                raise ValueError("Generation requires four valid attempts")
            records, trajectories = [], []
            for e in event["episodes"]:
                trajectory = read_json(Path(e["directory"]) / "trajectory.json")
                if (
                    digest(trajectory["messages"]) != e["history_sha256"]
                    or trajectory["initial_state_hash"] != event["initial_state_hash"]
                ):
                    raise ValueError("Feedback evidence changed")
                trajectories.append(trajectory)
                records.append(
                    {
                        "task_id": event["task_id"],
                        "trajectory_id": e["directory"],
                        "messages": trajectory["messages"],
                        "success": e["success"],
                        "initial_state_hash": trajectory["initial_state_hash"],
                    }
                )
            comparison = {
                "root_task": {
                    "task_id": event["task_id"],
                    "query": trajectories[0]["original_instruction"],
                },
                "parent_task": {
                    "task_id": event["task_key"],
                    "query": public_instruction(records[0]["messages"]),
                },
                "same_source_tasks": same_source_tasks(pool, event["task_id"]),
            }
            feedback = {
                "outcomes": event["outcomes"],
                "signals": event["signals"],
                "policy": event["policy"],
                "trajectories": records,
                "prior_success_groups": event.get("prior_success_groups", []),
            }
            explored = []
            if not any(t["api_counts"]["business"] for t in trajectories):
                explored.append(
                    engine.execute(
                        event["task_id"],
                        target / "exploration",
                        {"mode": "exploration", "context": feedback},
                    )
                )
            # Full four histories, source/parent comparisons, no scorer details or hidden labels.
            content = {
                "max_tasks": 2,
                "feedback": feedback,
                "task_comparison": comparison,
                "exploration": [{"messages": t["messages"]} for t in explored],
            }
            evidence(target / "input.json", content)
            proposals = engine.text(
                event["task_id"], target / "abstraction", GENERATION, content
            )
            if (
                not isinstance(proposals, dict)
                or set(proposals) != {"tasks"}
                or not isinstance(proposals["tasks"], list)
                or len(proposals["tasks"]) > 2
            ):
                raise ValueError("Expected at most two three-field candidates")
            for i, proposal in enumerate(proposals["tasks"]):
                candidate_dir = target / f"candidate_{i:02d}"
                item = {"directory": str(candidate_dir), "accepted": False}
                evidence(candidate_dir / "proposal.json", proposal)
                if (candidate_dir / "acceptance.json").exists():
                    item = read_json(candidate_dir / "acceptance.json")
                    if item["accepted"]:
                        accepted.append(read_json(candidate_dir / "accepted_task.json"))
                    context_attempts.append(item)
                    continue
                try:
                    task = candidate(proposal, records, feedback)
                    task["execution_protocol"] = identity()
                    task["task_id"] = identify(task)
                    duplicate = duplicate_of(
                        task["instruction"],
                        comparison,
                        pool + accepted,
                        event["task_id"],
                    )
                    if duplicate:
                        raise ValueError(
                            "Duplicate public task: " + duplicate["task_id"]
                        )
                    evidence(candidate_dir / "candidate.json", task)
                    new = admit(engine, task, candidate_dir)
                    accepted.append(new)
                    item.update(accepted=True, task_id=new["task_id"])
                except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
                    item.update(reason=str(exc), error_type=type(exc).__name__)
                write_json(candidate_dir / "acceptance.json", item)
                context_attempts.append(item)
        except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
            context_attempts.append(
                {
                    "directory": str(target),
                    "accepted": False,
                    "context_failure": True,
                    "reason": str(exc),
                }
            )
        write_json(
            target / "result.json", {"accepted": accepted, "attempts": context_attempts}
        )
        pool.extend(accepted)
        attempts.extend(context_attempts)
    result = {
        "accepted": [
            t
            for t in pool
            if t["task_id"] not in {x["task_id"] for x in request["pool"]}
        ],
        "attempts": attempts,
    }
    write_json(directory / "result.json", result)
    return result
