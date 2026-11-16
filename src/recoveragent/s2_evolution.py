"""Group-conditioned CoEvolve using the existing prompts, workers and admission."""

import time
import subprocess
from pathlib import Path

from .coevolve_generation import (
    CONTEXT_FIELDS,
    CONTEXT_PROMPT,
    ContextTooLong,
    Evolution,
    TaskQualityError,
)
from .io import digest, read_json, write_json
from .s2_state import evidence
from .v2_runtime import s2_execution


def rejection_reason(exc):
    if isinstance(exc, ContextTooLong):
        return "context_overflow"
    if isinstance(exc, TaskQualityError):
        return exc.quality_status
    text = str(exc).lower()
    for needles, category in [
        (("too long", "64k", "context window", "context_overflow"), "context_overflow"),
        (("duplicate",), "duplicate"),
        (("invented", "evidence", "references"), "insufficient_evidence"),
        (("reference", "solver", "semantic"), "solver_failed"),
        (("contract", "validation", "criterion"), "unsupported_validation"),
        (("budget", "reservation", "deadline"), "budget"),
        (("interrupt", "infrastructure", "unknown"), "infrastructure"),
    ]:
        if any(word in text for word in needles):
            return category
    return "format_or_execution"


class GroupEvolution(Evolution):
    def __init__(self, *args, budget_root, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget_root = Path(budget_root)
        self.active_event = None
        self.priced_cost_limit = s2_execution().get("teacher_priced_cost_limit", True)
        self.curriculum_admission = s2_execution().get("curriculum_admission", False)

    def remaining(self):
        if not self.priced_cost_limit:
            # A finite per-operation allowance; the user removed only the
            # cumulative price gate, not request/token/deadline limits.
            return self.cfg["s2"]["teacher_budget"]["priced_cost"]
        return super().remaining()

    def usage(self):
        total = {
            "requests": 0,
            "generated_tokens": 0,
            "input_tokens": 0,
            "seconds": 0.0,
            "priced_cost": 0.0,
            "unknown_reservations": 0,
        }
        for claim in self.budget_root.glob("**/s2_operation.json"):
            operation = read_json(claim)
            directory = Path(operation.get("output", claim.parent))
            usage = None
            if (
                operation["kind"] == "execute"
                and (directory / "trajectory.json").exists()
            ):
                record = read_json(directory / "trajectory.json")
                usage = record.get("usage")
            elif operation["kind"] == "text" and (directory / "usage.json").exists():
                usage = read_json(directory / "usage.json")
            if usage is None:
                reserve = operation["reserve"]
                for key, value in reserve.items():
                    total[key] += value
                total["unknown_reservations"] += 1
            else:
                total["requests"] += usage.get(
                    "provider_requests",
                    usage.get("model_requests", usage.get("requests", 1)),
                )
                total["generated_tokens"] += usage.get(
                    "generated_tokens", usage.get("output_tokens", 0)
                ) + usage.get("unknown_generation_reservation", 0)
                total["input_tokens"] += usage.get(
                    "input_tokens", usage.get("prompt_tokens", 0)
                )
                total["seconds"] += usage.get("wall_seconds", 0)
                total["priced_cost"] += usage["usage_priced_cost"] + usage.get(
                    "unknown_cost_reservation", 0
                )
        return total

    def reserve(self, directory, kind, *, output=None):
        limits = self.cfg["s2"]["teacher_budget"]
        usage = self.usage()
        requests = 80 if kind == "execute" else 1
        tokens = 16384 if kind == "execute" else self.settings["text_max_tokens"]
        price = self.cfg["teacher"]["pricing"]
        # Whole episode reservation is reconciled from its real usage receipt.
        reserve = {
            "requests": requests,
            "generated_tokens": tokens,
            "input_tokens": requests * self.cfg["teacher"]["billing_context_tokens"],
            "seconds": 1200
            if kind == "execute"
            else self.cfg["teacher"]["timeout_seconds"],
            "priced_cost": (
                requests
                * self.cfg["teacher"]["billing_context_tokens"]
                * price["input_per_million"]
                + tokens * price["output_per_million"]
            )
            / 1e6,
        }
        if any(
            usage[k] + reserve[k] > limits[k]
            for k in limits
            if k != "priced_cost" or self.priced_cost_limit
        ):
            raise RuntimeError("S2 separate Teacher budget exhausted")
        write_json(
            Path(directory) / "s2_operation.json",
            {
                "kind": kind,
                "reserve": reserve,
                **({"output": str(output)} if output is not None else {}),
            },
        )
        self.summary[self.cost_key] = usage["priced_cost"]

    def execute(self, source, directory, exploration):
        from .p3a import validate_config
        from .v2_teacher import configuration
        from .v2_tasks import RUNTIME

        validate_config(
            configuration(
                source,
                self.identity,
                self.deployment["appworld_root"],
                self.remaining(),
                recovery=False,
                runtime=self.cfg.get("execution_protocol", RUNTIME),
            )
        )
        directory = Path(directory)
        # run_teacher_episode owns its create-once output directory. The
        # reservation must not create it before that real entry is called.
        self.reserve(
            directory.with_name(directory.name + ".budget"), "execute", output=directory
        )
        return super().execute(source, directory, exploration)

    def raw_text(self, source, directory, system, content):
        # Measure first: an overflow consumes no paid reservation.
        from .teacher_collection import context_receipt
        from .v2_model import load_tokenizer

        if not hasattr(self, "context_tokenizer"):
            self.context_tokenizer = load_tokenizer(self.identity)
        from .io import canonical

        receipt = context_receipt(
            self.context_tokenizer,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": canonical(content)},
            ],
            [],
            self.settings["text_max_tokens"],
            {"max_sequence_tokens": 65536},
            self.identity,
        )
        if not receipt["within_budget"]:
            write_json(Path(directory) / "overflow.json", receipt)
            raise ContextTooLong("Measured S2 text input exceeds 64k")
        self.reserve(directory, "text")
        return super().text(source, directory, system, content)

    def text(self, source, directory, system, content):
        try:
            return self.raw_text(source, directory, system, content)
        except ContextTooLong:
            if Path(directory).name != "abstraction":
                raise
            by_rollout = {}
            for row in content["interaction_triplets"]:
                by_rollout.setdefault(row["rollout_id"], []).append(row)
            if len(by_rollout) != 2:
                raise
            write_json(
                Path(directory) / "fallback.json",
                {
                    "reason": "measured_combined_input_overflow",
                    "max_candidates_total": 2,
                    "rollout_ids": sorted(by_rollout),
                },
            )
            tasks = []
            for i, rows in enumerate(by_rollout.values()):
                value = self.raw_text(
                    source,
                    Path(directory) / f"separate_{i}",
                    system,
                    {
                        **content,
                        "interaction_triplets": rows,
                        "output_contract": content["output_contract"].replace(
                            "at most 2 tasks", "at most 1 tasks"
                        )
                        + " Return at most ONE candidate from this exploration.",
                    },
                )
                if not isinstance(value.get("tasks"), list) or len(value["tasks"]) > 1:
                    raise ValueError("Separate abstraction exceeded one candidate")
                tasks.extend(value["tasks"])
            return {"tasks": tasks}

    def summarize_history(self, source, directory, item, messages, offset=0):
        content = {
            "trajectory": item,
            "message_start": offset,
            "messages": [
                {"message_index": offset + i, "message": message}
                for i, message in enumerate(messages)
            ],
        }
        prompt = "Summarize all supplied public interaction evidence for later group comparison. Retain observed failures, constraints, actions/results and exact message indices. Return JSON {summary: string, evidence_refs: [{message_index: integer}]}. Copy message_index exactly from the supplied indexed entries; these are GLOBAL zero-based indices, never number this fragment locally. Do not infer hidden answers or scores."
        try:
            summary = self.text(source, directory, prompt, content)
            refs = summary.get("evidence_refs")
            if (
                not isinstance(summary.get("summary"), str)
                or not isinstance(refs, list)
                or not refs
                or any(
                    not isinstance(r, dict)
                    or type(r.get("message_index")) is not int
                    or not offset <= r["message_index"] < offset + len(messages)
                    for r in refs
                )
            ):
                raise ValueError("Untraceable per-trajectory summary")
            return [
                {
                    "trajectory": item,
                    "message_start": offset,
                    "message_count": len(messages),
                    "messages_sha256": digest(messages),
                    "summary": summary,
                }
            ]
        except ContextTooLong:
            if len(messages) < 2:
                raise
            # Split only at complete call boundaries; retain every original message.
            cuts = [
                i for i in range(1, len(messages)) if messages[i].get("role") != "tool"
            ]
            if not cuts:
                raise
            cut = min(cuts, key=lambda i: abs(i - len(messages) / 2))
            return self.summarize_history(
                source, directory / "left", item, messages[:cut], offset
            ) + self.summarize_history(
                source, directory / "right", item, messages[cut:], offset + cut
            )

    def context(self, event, directory):
        self.active_event = event
        histories = []
        for item in event["episodes"]:
            messages = read_json(Path(item["directory"]) / "history.json")["messages"]
            if digest(messages) != item["history_sha256"]:
                raise ValueError("Group feedback history changed")
            histories.append({"trajectory": item, "messages": messages})
        if len(histories) != 4:
            raise ValueError("Context requires all four completed trajectories")
        content = {"group": event, "trajectories": histories}
        prompt = CONTEXT_PROMPT + (
            " Analyze the complete four-attempt group. For boundary compare successful and failed executions. "
            "For all_failed identify common errors and retain the core skill while reducing unrelated complexity. "
            "For forgetting cite only supplied successes BEFORE this group; within-group success is not forgetting. "
            "Preserve the six output fields. Outcome flags are task-level signals, not hidden scoring details."
        )
        if self.curriculum_admission:
            prompt = CONTEXT_PROMPT + (
                " Analyze all four attempts. Compare successes and failures, and separate observed evidence "
                "from hypotheses about missing skills. For all_failed, consider simpler component tasks; "
                "retrieval itself may still be weak even if later steps also fail. Do not require a new task "
                "to retain every operation of the parent. For forgetting cite only success BEFORE this group. "
                "Preserve the six output fields and all four trajectory references."
            )
        try:
            result = self.text(event["task_id"], directory, prompt, content)
        except ContextTooLong:
            summarized = []
            for i, row in enumerate(histories):
                summarized.extend(
                    self.summarize_history(
                        event["task_id"],
                        directory / f"trajectory_{i}",
                        row["trajectory"],
                        row["messages"],
                    )
                )
            result = self.text(
                event["task_id"],
                directory / "aggregate",
                prompt,
                {
                    "group": event,
                    "traceable_summaries": summarized,
                },
            )
        if set(result) != CONTEXT_FIELDS:
            raise ValueError("Malformed six-field group context")
        evidence(Path(directory) / "group_input.json", content)
        evidence(Path(directory) / "context_result.json", result)
        return result

    def quality(self, task, directory):
        result = super().quality(task, directory)
        if self.curriculum_admission:
            return result
        judgment = self.text(
            task["source_task_id"],
            Path(directory) / "feedback_fit",
            "Verify whether the proposed public goal exercises the supplied observed learning need. Return JSON {aligned: boolean, retains_core: boolean, reduces_irrelevant_complexity: boolean, core_requirement: string, difference: string, reason: string}. For all_failed retain the core skill and reduce object count or unrelated steps. A merely unrelated easier task fails. Use public evidence only; uncertainty means false.",
            {
                "query": task["instruction"],
                "feedback": self.active_event,
                "context": self.current_context,
            },
        )
        if judgment.get("aligned") is not True or any(
            not isinstance(judgment.get(k), str) or not judgment[k].strip()
            for k in ("core_requirement", "difference", "reason")
        ):
            raise TaskQualityError(
                "feedback_mismatch",
                "Candidate does not substantiate feedback alignment",
            )
        if "all_failed" in self.active_event["signals"] and not (
            judgment.get("retains_core") is True
            and judgment.get("reduces_irrelevant_complexity") is True
        ):
            raise TaskQualityError(
                "feedback_mismatch",
                "All-failed candidate must retain the core skill and simplify unrelated complexity",
            )
        return result

    def run_context(self, event, directory):
        directory = Path(directory)
        result_path = directory / "s2_context_result.json"
        if result_path.exists():
            result = read_json(result_path)
            if result["event_sha256"] != digest(event):
                raise ValueError("Resumed context identity changed")
            return result
        started = time.time()
        claim = directory / "s2_context_claim.json"
        interrupted = claim.exists()
        before = self.usage()
        error = None
        if not interrupted:
            write_json(claim, {"event": event, "started_epoch": started})
            try:
                context = self.context(event, directory / "context")
                self.current_context = context
                self.generate(event["task_id"], context, directory)
            except (
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
                TimeoutError,
                subprocess.SubprocessError,
            ) as exc:
                error = {
                    "category": rejection_reason(exc),
                    "reason": str(exc),
                    "type": type(exc).__name__,
                }
        else:
            error = {
                "category": "infrastructure",
                "reason": "Interrupted context retained; no paid operation replay",
            }
        accepted = [
            read_json(p) for p in sorted(directory.glob("*/accepted_task.json"))
        ]
        proposals = sorted(directory.glob("*/proposal.json"))
        attempts = []
        for path in proposals:
            receipt = path.parent / "acceptance.json"
            item = (
                read_json(receipt)
                if receipt.exists()
                else {
                    "accepted": False,
                    "reason": "Interrupted candidate",
                    "directory": str(path.parent),
                }
            )
            if not item.get("accepted"):
                item = {
                    **item,
                    "rejection_category": item.get("quality_status")
                    or rejection_reason(ValueError(item.get("reason", "unknown"))),
                }
            attempts.append(item)
        # Even a partial context cannot lose its already accepted tasks at restart.
        lineage = {
            "source_task_id": event["task_id"],
            "direct_parent_task_id": event["task_key"],
            "source_policy": event["policy"],
            "group_id": event["group_id"],
            "source_outcomes": event["outcomes"],
            "source_signals": event["signals"],
            "four_trajectories": event["episodes"],
            "context_directory": str(directory / "context"),
        }
        result = {
            "event_sha256": digest(event),
            "lineage": lineage,
            "accepted": accepted,
            "attempts": attempts,
            "candidate_count": len(proposals),
            "error": error,
            "explorations": len(list(directory.glob("*_explore_*/trajectory.json"))),
            "exploration_attempts": len(
                list(directory.glob("*_explore_*/s2_operation.json"))
            ),
            "context_completed": (directory / "context/context_result.json").exists(),
            "teacher_usage_before": before,
            "teacher_usage_after": self.usage(),
            "wall_seconds": time.time() - started,
            "interrupted": interrupted,
        }
        write_json(result_path, result)
        return result
