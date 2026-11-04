"""P3-C step adapter for the already validated same-host deadline contract."""

from .io import write_json
from .m1_vgpu_reference_backend import (
    GenerationDeadline,
    VARIANT,
    remaining,
    validate_deadline_response,
)
from .student_evaluation import execution_clock, request_json


def session_class(parent, url):
    class DeadlineSession(parent):
        def step(self, unused_generate):
            def generate(request):
                deadline = self.started + self.wall_seconds
                left = remaining(deadline, execution_clock())
                if left <= 0:
                    self.usage["unknown_generation_reservation"] = 0
                    self.deadline_receipt = {
                        "status": "not_dispatched_deadline",
                        "deadline_monotonic": deadline,
                    }
                    raise GenerationDeadline()
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
                    response.get("runtime_variant") != VARIANT
                    or response.get("deadline_monotonic") != deadline
                ):
                    raise ValueError("Backend/client deadline contract mismatch")
                if response.get("finish_reason") == "deadline":
                    tokens = validate_deadline_response(response, wire)
                    self.usage["generated_tokens"] += len(tokens)
                    self.usage["unknown_generation_reservation"] = 0
                    self.deadline_receipt = response
                    raise GenerationDeadline()
                return response

            try:
                return super().step(generate)
            except GenerationDeadline:
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
