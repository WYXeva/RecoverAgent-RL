"""Loss-preserving coalescing of actual append-only requests within one episode."""

import copy


def coalesce(rows, *, max_target_tokens=4096):
    if type(max_target_tokens) is not int or max_target_tokens < 1:
        raise ValueError("Invalid coalescing target-token cap")
    chains = []
    for original in rows:
        row = copy.deepcopy(original)
        row["tool_mask"] = [1] * len(row["completion_ids"])
        row["request_refs"] = [
            {k: row[k] for k in ("request_sha256", "response_sha256")}
        ]
        if chains:
            previous = chains[-1]
            prefix = previous["prompt_ids"] + previous["completion_ids"]
            prompt = row["prompt_ids"]
            if (
                len(prompt) >= len(prefix)
                and prompt[: len(prefix)] == prefix
                # Bound LM-head logits by the existing per-reply output cap.
                # Start another chain; never remove or truncate targets.
                and sum(previous["tool_mask"]) + len(row["completion_ids"])
                <= max_target_tokens
                and all(
                    row.get(k) == previous.get(k)
                    for k in (
                        "temperature",
                        "top_p",
                        "logprob_contract",
                        "student_input_encoding",
                    )
                )
            ):
                gap = prompt[len(prefix) :]
                previous["completion_ids"] += gap + row["completion_ids"]
                previous["logprobs"] += [0.0] * len(gap) + row["logprobs"]
                previous["tool_mask"] += [0] * len(gap) + row["tool_mask"]
                previous["request_refs"] += row["request_refs"]
                continue
        chains.append(row)
    return chains


def offload_row(setting, sequence_tokens):
    """An explicit measured threshold, never a guessed free-memory heuristic."""
    if setting in (None, "none"):
        return False
    if setting == "cpu":
        return True
    if isinstance(setting, dict) and set(setting) == {"cpu_above_tokens"}:
        threshold = setting["cpu_above_tokens"]
        if type(threshold) is int and threshold >= 0:
            return sequence_tokens > threshold
    raise ValueError("Invalid activation_offload setting")
