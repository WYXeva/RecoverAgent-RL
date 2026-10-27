"""EGTM v1: exact observations, conservative validity, and auditable progress.

No environment/evaluator access. The only ingestion point is a completed Student
batch. Assistant prose is an inferred plan, never verified task completion.
"""

import copy
import json
from pathlib import Path

from .adapter import call_code
from .harness import public_tool_error
from .io import canonical, digest, read_json, write_json
from .memory_store import EvidenceIndex, json_value, leaves
from .teacher_history import validate_history

VERSION = "egtm-task-memory-v1"
RUNTIME = "egtm_task_memory_20261027"
DEFAULT_CONFIG = {
    "enabled": False,
    "version": VERSION,
    "trigger_tokens": 24576,
    "recent_batches": 2,
    "max_facts": 48,
    "retrieval_limit": 3,
}


def memory_config(config=None):
    value = {**DEFAULT_CONFIG, **(config or {})}
    if set(value) != set(DEFAULT_CONFIG) or value["version"] != VERSION:
        raise ValueError("Unsupported EGTM configuration")
    if type(value["enabled"]) is not bool or any(
        type(value[k]) is not int or value[k] <= 0
        for k in ("trigger_tokens", "recent_batches", "max_facts", "retrieval_limit")
    ):
        raise ValueError("Invalid EGTM bounds")
    return value


class TaskMemory:
    def __init__(self, task_id, messages, config):
        validate_history(messages)
        if not messages or any(m.get("tool_calls") for m in messages):
            raise ValueError(
                "Existing interactions require verified Memory restoration"
            )
        self.task_id, self.config = task_id, memory_config(config)
        self.initial_messages = copy.deepcopy(messages)
        self.index = EvidenceIndex(task_id)
        self.batches, self.facts, self.operations, self.plans = [], [], [], []
        self.methods = {}
        self.stale_sources = {}

    def observe(self, messages, event):
        """Validate the entire boundary before committing any derived fact."""
        validate_history(messages)
        start = event["assistant_index"]
        calls = messages[start].get("tool_calls", [])
        end = start + 1 + len(calls)
        verdicts = event.get("harness_verdicts", [None] * len(calls))
        if (
            event["status"] != "complete_boundary"
            or not calls
            or event.get("inflight_call_id")
            or len(verdicts) != len(calls)
            or event["history_hash"] != digest(messages[:start])
            or event["tool_results"] != messages[start + 1 : end]
            or event["dispatched_count"] != len(calls)
        ):
            raise ValueError("Memory requires a complete, matching tool batch")
        receipt = {
            "start": start,
            "end": end,
            "request_id": event["request_id"],
            "tools": copy.deepcopy(event["tools"]),
            "executed": [v is None for v in verdicts],
            "prefix_hash": digest(messages[:end]),
        }
        if self.batches and start < self.batches[-1]["end"]:
            raise ValueError("Memory batch already committed")
        # Preflight every call and result before changing the index or validity.
        for call in calls:
            call_code(
                call["function"]["name"],
                call["function"]["arguments"],
                receipt["tools"],
            )
            if call["id"] in self.index.keys["source_id"]:
                raise ValueError("Duplicate memory source")
        for offset, call in enumerate(calls):
            observation = messages[start + 1 + offset]
            if observation.get("name") != call["function"]["name"]:
                raise ValueError("Memory tool name mismatch")
        self._commit(messages, receipt)

    def _commit(self, messages, receipt):
        start, end = receipt["start"], receipt["end"]
        assistant = messages[start]
        if assistant.get("content"):
            self.plans.append(
                {
                    "status": "inferred",
                    "text": assistant["content"],
                    "assistant_index": start,
                    "source_hash": digest(assistant),
                }
            )
        for offset, (call, executed) in enumerate(
            zip(assistant["tool_calls"], receipt["executed"])
        ):
            tool = call["function"]["name"]
            observation = messages[start + 1 + offset]
            row = {
                "task_id": self.task_id,
                "source_id": call["id"],
                "tool_name": tool,
                "arguments": json.loads(call["function"]["arguments"]),
                "content": observation["content"],
                "executed": executed,
                "assistant_index": start,
                "result_index": start + 1 + offset,
                "boundary_end": end,
                "call_sha256": digest(call),
                "result_sha256": digest(observation),
                "prefix_sha256": receipt["prefix_hash"],
            }
            self.index.append(row)
            if not executed:
                continue  # AnswerGuard feedback is not an AppWorld observation.
            error = public_tool_error(row["content"])
            value = json_value(row["content"])
            documentation = tool.startswith("api_docs__")
            method = self.methods.get(tool)
            # Unknown APIs and all non-GET methods may have side effects. In v1
            # invalidate across apps too: transfers can affect another app.
            possible_write = not documentation and method not in {"GET", "HEAD"}
            if possible_write:
                for old in self.operations:
                    if (
                        not old["tool_name"].startswith("api_docs__")
                        and old["outcome"] == "returned"
                    ):
                        self.stale_sources[old["tool_call_id"]] = call["id"]
                for fact in self.facts:
                    if fact["kind"] != "documentation":
                        fact.update(status="stale", invalidated_by=call["id"])
            operation = {
                "tool_call_id": call["id"],
                "tool_name": tool,
                "arguments": row["arguments"],
                "evidence_ref": self.reference(row),
                "outcome": "error_observed" if error else "returned",
                "effect": "unknown_requires_verification" if possible_write else "read",
                "query_identity": digest([tool, row["arguments"]]),
            }
            operation["repeat_of"] = next(
                (
                    o["tool_call_id"]
                    for o in reversed(self.operations)
                    if o["query_identity"] == operation["query_identity"]
                ),
                None,
            )
            if not error and not possible_write and operation["repeat_of"]:
                # A fresh read supersedes only this exact query, including every
                # page/filter/sort argument. It says nothing about other pages.
                superseded = {
                    o["tool_call_id"]
                    for o in self.operations
                    if o["query_identity"] == operation["query_identity"]
                    and o["outcome"] == "returned"
                }
                self.stale_sources.update({source: call["id"] for source in superseded})
                for fact in self.facts:
                    if fact["tool_call_id"] in superseded:
                        fact.update(status="stale", invalidated_by=call["id"])
            self.operations.append(operation)
            if (
                tool == "api_docs__show_api_doc"
                and not error
                and isinstance(value, dict)
            ):
                name = f"{value.get('app_name')}__{value.get('api_name')}"
                if value.get("method") in {
                    "GET",
                    "HEAD",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                }:
                    self.methods[name] = value["method"]
            if not error and value is not None:
                for path, scalar in leaves(value):
                    # Large text remains recoverable in L2/L3, not summarized.
                    if len(canonical(scalar)) > 256:
                        continue
                    self.facts.append(
                        {
                            "tool_call_id": call["id"],
                            "path": path,
                            "value": scalar,
                            "status": "observed",
                            "kind": "documentation" if documentation else "result",
                            "evidence_ref": self.reference(row),
                        }
                    )
        self.batches.append(copy.deepcopy(receipt))

    @staticmethod
    def reference(row):
        return {
            "tool_call_id": row["source_id"],
            "message_index": row["result_index"],
            "sha256": row["result_sha256"],
        }

    def validate_boundary(self, messages, tools):
        validate_history(messages)
        if messages[: len(self.initial_messages)] != self.initial_messages:
            raise ValueError("Memory task constraints changed")
        covered = {r["source_id"] for r in self.index.rows}
        calls = {c["id"] for m in messages for c in m.get("tool_calls", [])}
        if calls != covered:
            raise ValueError("History and Memory evidence boundaries differ")
        public = {t["function"]["name"]: t for t in tools}
        for batch in self.batches:
            if (
                batch["end"] > len(messages)
                or digest(messages[: batch["end"]]) != batch["prefix_hash"]
            ):
                raise ValueError("Future or altered Memory source history")
            if any(t != public.get(t["function"]["name"]) for t in batch["tools"]):
                raise ValueError("Memory schema provenance changed")

    def working_view(self, selected_ids=()):
        """Bounded view, explicitly incomplete. No automatic business planning."""
        limit = self.config["max_facts"]
        facts = [f for f in self.facts if f["kind"] != "documentation"]
        ranked = sorted(
            enumerate(facts),
            key=lambda pair: (
                pair[1]["tool_call_id"] in selected_ids,
                pair[1]["status"] == "observed",
                pair[0],
            ),
            reverse=True,
        )
        chosen = [f for _, f in ranked[:limit]]
        return {
            "version": VERSION,
            "task_constraints": "Original system/user messages retained verbatim.",
            "facts": chosen,
            "facts_omitted": len(facts) - len(chosen),
            "recent_operations": self.operations[-8:],
            "operation_count": len(self.operations),
            "latest_proposed_steps": self.plans[-1:]
            or [
                {
                    "status": "inferred",
                    "text": "No explicit plan observed; remaining requirements are unknown.",
                }
            ],
            "observed_errors": [
                o for o in self.operations if o["outcome"] == "error_observed"
            ][-4:],
            "needs_revalidation": sorted(self.stale_sources),
            "completed_task_requirements": "unknown; a returned call is not proof of task completion",
        }

    @property
    def identity(self):
        return {
            "version": VERSION,
            "config_sha256": digest(self.config),
            "index": self.index.identity,
            "state_sha256": digest(self.state()),
        }

    def state(self):
        return {
            "version": VERSION,
            "task_id": self.task_id,
            "config": self.config,
            "initial_messages": self.initial_messages,
            "batches": self.batches,
            "facts": self.facts,
            "operations": self.operations,
            "plans": self.plans,
            "methods": self.methods,
            "stale_sources": self.stale_sources,
            "index": self.index.identity,
        }

    def save(self, destination, messages, tools):
        self.validate_boundary(messages, tools)
        destination = Path(destination)
        self.index.save(destination / "index.jsonl")
        write_json(destination / "state.json", self.state())

    @classmethod
    def load(cls, source, messages, tools):
        """Recompute everything from the same-time history; hashes alone are not proof."""
        source = Path(source)
        state = read_json(source / "state.json")
        if state["version"] != VERSION:
            raise ValueError("Unknown Memory version")
        memory = cls(state["task_id"], state["initial_messages"], state["config"])
        for batch in state["batches"]:
            start, end = batch["start"], batch["end"]
            if not 0 <= start < end <= len(messages):
                raise ValueError("Future Memory boundary")
            memory.observe(
                messages[:end],
                {
                    "status": "complete_boundary",
                    "assistant_index": start,
                    "request_id": batch["request_id"],
                    "history_hash": digest(messages[:start]),
                    "tools": batch["tools"],
                    "tool_results": messages[start + 1 : end],
                    "dispatched_count": end - start - 1,
                    "harness_verdicts": [
                        None if e else {"not_executed": True} for e in batch["executed"]
                    ],
                },
            )
        memory.validate_boundary(messages, tools)
        rows = [
            json.loads(line)
            for line in (source / "index.jsonl").read_text().splitlines()
        ]
        if memory.state() != state or rows != memory.index.rows:
            raise ValueError("Memory facts/index do not match their original evidence")
        return memory
