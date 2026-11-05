"""P3-C step adapter for the already validated same-host deadline contract."""

from .io import write_json
from .m1_vgpu_reference_backend import VARIANT as VARIANT
from .student_evaluation import execution_clock, request_json
import math


def remaining_for_budget(deadline, now, cap):
    if cap not in {600, 1200} or type(deadline) not in {int, float}:
        raise ValueError("Invalid versioned session deadline")
    value = deadline - now
    if not math.isfinite(value) or value > cap:
        raise ValueError("Invalid session deadline")
    return max(0.0, value)


def session_class(parent, url, *, contract=None):
    from . import m1_vgpu_reference_backend as original

    contract = original if contract is None else contract

    class DeadlineSession(parent):
        def step(self, unused_generate):
            def generate(request):
                deadline = self.started + self.wall_seconds
                left = contract.remaining(deadline, execution_clock())
                if left <= 0:
                    self.usage["unknown_generation_reservation"] = 0
                    self.deadline_receipt = {
                        "status": "not_dispatched_deadline",
                        "deadline_monotonic": deadline,
                    }
                    raise contract.GenerationDeadline()
                wire = dict(request, deadline_monotonic=deadline)
                number = self.usage["model_requests"]
                if self.output:
                    write_json(
                        self.output
                        / "requests"
                        / f"{number:03d}.deadline_request.json",
                        wire,
                    )
                response = request_json(url, wire, timeout=left + 5)
                if self.output:
                    write_json(
                        self.output
                        / "requests"
                        / f"{number:03d}.deadline_response.json",
                        response,
                    )
                if (
                    response.get("runtime_variant") != contract.VARIANT
                    or response.get("deadline_monotonic") != deadline
                ):
                    raise ValueError("Backend/client deadline contract mismatch")
                if response.get("finish_reason") == "deadline":
                    tokens = contract.validate_deadline_response(response, wire)
                    self.usage["generated_tokens"] += len(tokens)
                    self.usage["unknown_generation_reservation"] = 0
                    self.deadline_receipt = response
                    raise contract.GenerationDeadline()
                return response

            try:
                return super().step(generate)
            except contract.GenerationDeadline:
                # The original raw rejection/event remains immutable audit evidence.
                self.stopped, self.termination = True, "budget_exhausted"
                event = {
                    "status": "server_confirmed_generation_deadline",
                    "receipt": self.deadline_receipt,
                    "tool_results": [],
                    "dispatched_count": 0,
                    "partial_tool_calls_executed": False,
                    "usage": self.usage,
                }
                if self.output:
                    write_json(
                        self.output / "generation_deadline_resolution.json", event
                    )
                return event

    return DeadlineSession
