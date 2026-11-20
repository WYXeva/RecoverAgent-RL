"""Conservative repeated-error stopping; no business decisions or answer repair."""

import math
import re
from datetime import date, datetime, timedelta

from recoveragent.io import canonical, digest

REVISION = "codeact_repeated_error_v1"
NOTICE = (
    "The identical code has repeatedly returned the identical execution error. "
    "No progress was detected. Use the observed error and available documentation "
    "to change the code or parameters; do not repeat the unchanged failing block. "
    "Two further identical, unchanged failures will end this attempt. "
    "Earlier operations may have executed; inspect their results before retrying."
)


def execution_identity(config):
    setting = config.get("student_execution")
    if setting is None:
        return None
    if setting != {"revision": REVISION, "warn_after": 3, "stop_after": 5}:
        raise ValueError("Unknown Student repeated-error execution settings")
    return {**setting, "notice_sha256": digest(NOTICE)}


def value_key(value, remaining):
    """Hash only understood Python values; opaque/mutable objects disable stopping."""
    remaining[0] -= 1
    if remaining[0] < 0:
        raise ValueError("Python state too complex for conservative progress check")
    if value is None or type(value) in (str, bool, int):
        return [type(value).__name__, value]
    if type(value) is float and math.isfinite(value):
        return ["float", value]
    if type(value) in (list, tuple):
        return [type(value).__name__, [value_key(v, remaining) for v in value]]
    if type(value) in (dict, set, frozenset):
        rows = (
            [
                [value_key(k, remaining), value_key(v, remaining)]
                for k, v in value.items()
            ]
            if type(value) is dict
            else [value_key(v, remaining) for v in value]
        )
        return [type(value).__name__, sorted(rows, key=canonical)]
    if isinstance(value, (datetime, date, timedelta)):
        return [type(value).__qualname__, str(value)]
    raise ValueError("Untracked Python value")


class RepeatedError:
    def __init__(self, world, setting):
        self.world, self.setting = world, setting
        self.baseline = dict(world.shell.user_ns)
        self.key, self.count = None, 0

    def fingerprint(self):
        # IPython's own input/output history advances every turn, not task progress.
        internal = {
            "In",
            "Out",
            "_ih",
            "_oh",
            "_dh",
            "_i",
            "_ii",
            "_iii",
            "_",
            "__",
            "___",
        }
        remaining = [100000]
        values = {}
        try:
            for name, value in self.world.shell.user_ns.items():
                if name in internal or re.fullmatch(r"_i?\d+", name):
                    continue
                # Initial runtime objects are not Student variables. Containers
                # still need tracking, including their in-place edits.
                if (
                    name in self.baseline
                    and value is self.baseline[name]
                    and type(value)
                    not in (
                        str,
                        bool,
                        int,
                        float,
                        list,
                        tuple,
                        dict,
                        set,
                        frozenset,
                        type(None),
                    )
                ):
                    continue
                values[name] = value_key(value, remaining)
            return digest(
                {"python": values, "models": self.world.models.model_hashes()}
            )
        except (ValueError, TypeError, RecursionError, AttributeError):
            return None

    def observe(self, code, observation):
        # Retryable transport/timeouts/rate limits and normal outputs do not count.
        deterministic = observation.startswith("Execution failed.") and bool(
            re.search(
                r"Response status code is (?:400|401|403|404|405|409|422)\b",
                observation,
            )
            or re.search(
                r"(?:NameError|KeyError|TypeError|AttributeError|ValueError|SyntaxError):|Syntax error in line:",
                observation,
            )
        )
        state = self.fingerprint() if deterministic else None
        key = digest([code, observation, state]) if state is not None else None
        self.count = (
            self.count + 1
            if key is not None and key == self.key
            else int(key is not None)
        )
        self.key = key
        return {
            "revision": REVISION,
            "consecutive_identical_errors": self.count,
            "state_sha256": state,
            "warn": self.count == self.setting["warn_after"],
            "stop": self.count >= self.setting["stop_after"],
        }
