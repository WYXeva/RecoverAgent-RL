"""Reuse native Teacher transport/usage, without legacy experiment controllers."""

import copy
import time

from recoveragent.io import write_json
from recoveragent.teacher import (
    TeacherPolicy,
    TeacherTransportError,
    response_usage,
    response_model_matches,
    usage_cost,
)
from recoveragent.teacher_context import receipt

from .protocol import TOOLS, BudgetExhausted, now


class Teacher:
    def __init__(self, config, credentials):
        self.config = config
        self.policy = TeacherPolicy(
            {
                "teacher": config["teacher"],
                "authorization": config["authorization"],
                "tool_mode": "appworld_code",
            },
            credentials,
        )
        self.usage = {
            "requests": 0,
            "prompt_tokens": 0,
            "generated_tokens": 0,
            "cached_prompt_tokens": 0,
            "priced_cost": 0.0,
            "provider_cost": 0.0,
            "unknown_requests": 0,
            "unknown_output_reserved": 0,
            "seconds": 0.0,
        }

    def generate(self, messages, directory, index, remaining_tokens):
        maximum = min(self.config["teacher"]["max_new_tokens"], remaining_tokens)
        payload = self.policy.build_request(messages, copy.deepcopy(TOOLS), maximum)
        audit = receipt(payload, self.config["teacher_context"])
        write_json(directory / f"{index:03d}.context.json", audit)
        if not audit["within_budget"]:
            raise BudgetExhausted(
                "Teacher context window exhausted; history was not truncated"
            )
        reserve = usage_cost(
            {
                "prompt_tokens": self.config["teacher"]["billing_context_tokens"],
                "generated_tokens": maximum,
                "cached_prompt_tokens": 0,
            },
            self.config["teacher"]["pricing"],
        )
        for retry in range(self.config["limits"]["request_retries"] + 1):
            if (
                self.usage["generated_tokens"]
                + self.usage["unknown_output_reserved"]
                + maximum
                > self.config["limits"]["max_generated_tokens"]
            ):
                raise BudgetExhausted(
                    "Teacher output budget including unknown requests exhausted"
                )
            if self.usage["requests"] >= self.config["limits"]["max_requests"]:
                raise BudgetExhausted("Teacher request budget exhausted")
            if (
                self.usage["priced_cost"] + reserve
                > self.config["limits"]["attempt_price_cap_usd"]
            ):
                raise BudgetExhausted("Teacher per-attempt cost budget exhausted")
            stem = f"{index:03d}_{retry}"
            write_json(directory / f"{stem}.request.json", payload)
            self.usage["requests"] += 1
            started = now()
            try:
                response = self.policy.transport(payload)
            except TeacherTransportError as exc:
                self.usage["unknown_requests"] += 1
                self.usage["unknown_output_reserved"] += maximum
                self.usage["priced_cost"] += reserve
                write_json(
                    directory / f"{stem}.error.json",
                    {
                        "error": str(exc),
                        "diagnostic": exc.diagnostic,
                        "reserved_cost": reserve,
                        "usage_unknown": True,
                    },
                )
                # Do not multiply rejected credentials/quota calls within an episode.
                if retry == self.config["limits"]["request_retries"] or any(
                    x in str(exc) for x in ("401", "403", "429")
                ):
                    raise
                time.sleep(2**retry)
                continue
            finally:
                self.usage["seconds"] += now() - started
            write_json(directory / f"{stem}.response.json", response)
            if not response_model_matches(
                self.config["teacher"], response.get("model")
            ):
                raise ValueError("Teacher response model changed")
            usage = response_usage(response)
            for key in ("prompt_tokens", "generated_tokens", "cached_prompt_tokens"):
                self.usage[key] += usage[key]
            self.usage["priced_cost"] += usage_cost(
                usage, self.config["teacher"]["pricing"]
            )
            self.usage["provider_cost"] += response.get("usage", {}).get("cost", 0) or 0
            return response

    def remember(self, index, response):
        self.policy.remember_response(index, response)
