"""One task-local capacity retry; immutable original attempts and bounded resume."""

from pathlib import Path

from recoveragent.io import digest, read_json, write_json

REVISION = "codeact_sampling_fallback_v1"


class SamplingCapacityError(RuntimeError):
    """A sampling process/backend failed, rather than a business objective."""


def fallback_enabled(config):
    setting = config.get("online", {}).get("sampling_fallback")
    if setting is None:
        return False
    if setting != {"revision": REVISION, "concurrency": 2, "max_retries": 1} or (
        type(setting.get("concurrency")) is not int
        or type(setting.get("max_retries")) is not int
        or config["online"].get("sampling_concurrency") != 4
    ):
        raise ValueError("Sampling fallback requires four-to-two with one retry")
    return True


def resource_error(text):
    text = str(text).lower()
    return any(
        phrase in text
        for phrase in (
            "out of memory",
            "outofmemoryerror",
            "memoryerror",
            "cannot allocate memory",
            "resource temporarily unavailable",
        )
    )


def retryable(row):
    if row.get("reward_valid") is not False or row.get("failure_stage") in (
        "judging",
        "evaluation",
    ):
        return False
    fault = str(row.get("fault", ""))
    return bool(
        row.get("failure_type") == "SamplingCapacityError"
        or resource_error(fault)
        or (
            fault.startswith("Student vLLM request failed:")
            and any(
                marker in fault.lower()
                for marker in (
                    "connection",
                    "timed out",
                    "timeout",
                    "500 server error",
                    "502 server error",
                    "503 server error",
                    "504 server error",
                )
            )
        )
    )


def sample_with_fallback(directory, binding, execute, recover, deadline_check):
    """execute(width, members, path); recover preserves completed rows after a crash.

    No world state is reconstructed. Retry uses the same actor/seed in a new clean
    environment. This function never changes a caller's default concurrency.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    identity = {"revision": REVISION, **binding}
    identity_path = directory / "sampling_binding.json"
    if identity_path.exists():
        if read_json(identity_path) != identity:
            raise ValueError("Sampling fallback actor/task/config binding changed")
    else:
        write_json(identity_path, identity)

    def phase(width, members, name):
        path = directory / name
        outcome = path / "phase_outcome.json"
        if outcome.exists():
            return read_json(outcome)
        deadline_check()
        # A crash while retrying does not grant a third clean attempt on resume.
        intent = path / "phase_intent.json"
        if intent.exists():
            result = path / "result.json"
            rows = (
                read_json(result)["episodes"]
                if result.exists()
                else recover(
                    path,
                    members,
                    "Interrupted sampling phase; original attempts retained",
                )
            )
        else:
            write_json(intent, {"concurrency": width, "members": members})
            try:
                rows = execute(width, members, path)
            except SamplingCapacityError as exc:
                rows = recover(path, members, str(exc))
        if len(rows) != len(members):
            raise ValueError("Sampling phase returned an incomplete member mapping")
        write_json(
            outcome, {"concurrency": width, "members": members, "episodes": rows}
        )
        return read_json(outcome)

    primary = phase(4, list(range(4)), "primary")
    rows = list(primary["episodes"])
    plan_path = directory / "fallback_plan.json"
    plan = {
        "primary_sha256": digest(primary),
        "retry_members": [i for i, row in enumerate(rows) if retryable(row)],
        "concurrency": 2,
        "max_retries": 1,
    }
    if plan_path.exists():
        if read_json(plan_path) != plan:
            raise ValueError("Sampling retry plan changed")
    else:
        write_json(plan_path, plan)
    discarded = []
    members = plan["retry_members"]
    phases = [primary]
    if members:
        retry = phase(2, members, "retry_2")
        phases.append(retry)
        for member, replacement in zip(members, retry["episodes"], strict=True):
            discarded.append(rows[member])
            rows[member] = replacement
    return {
        "episodes": rows,
        "sampling_concurrency": 4,
        "sampling_fallback": {
            "revision": REVISION,
            "triggered": bool(members),
            "retry_members": members,
            "phase_concurrency": [p["concurrency"] for p in phases],
            "attempts": 4 + len(members),
            "discarded_attempts": discarded,
        },
    }
