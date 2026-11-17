"""Scoped S2 Teacher context estimates, independent of Student training lengths."""

from functools import lru_cache
import os
from pathlib import Path

from .demonstrations import sha256
from .io import canonical, digest


@lru_cache(maxsize=4)
def tokenizer(repo, revision, expected_sha, directory):
    from tokenizers import Tokenizer

    if directory:
        path = Path(directory) / "tokenizer.json"
    else:
        from huggingface_hub import hf_hub_download

        path = Path(hf_hub_download(repo, "tokenizer.json", revision=revision))
    if sha256(path) != expected_sha:
        raise ValueError("Teacher tokenizer artifact SHA mismatch")
    return Tokenizer.from_file(str(path))


def receipt(request, settings):
    """Count the full serialized input with the matching pinned tokenizer.

    OpenCode's server-side template is not exposed. JSON serialization plus the
    explicit framing allowance is an estimate, never provider-exact usage.
    Reasoning history and tool schemas are included; nothing is truncated.
    """
    if request["model"] != settings["model"]:
        raise ValueError("Teacher context/model identity differs")
    if settings["counting_method"] != "matching_tokenizer_json_estimate_v1":
        raise ValueError("Unknown Teacher context counting method")
    spec = settings["tokenizer"]
    counter = tokenizer(
        spec["repo"],
        spec["revision"],
        spec["sha256"],
        os.environ.get("RECOVERAGENT_TEACHER_TOKENIZER_DIR", ""),
    )
    content = {
        "messages": request["messages"],
        "tools": request.get("tools", []),
        "thinking": request.get("thinking"),
    }
    serialized = canonical(content)
    tokens = len(counter.encode(serialized, add_special_tokens=False).ids)
    estimate = tokens + settings["framing_allowance_tokens"]
    reserved = request["max_tokens"]
    return {
        "revision": settings["revision"],
        "model": settings["model"],
        "counting_method": settings["counting_method"],
        "provider_exact": False,
        "tokenizer": spec,
        "serialized_input_tokens": tokens,
        "framing_allowance_tokens": settings["framing_allowance_tokens"],
        "input_tokens_estimate": estimate,
        "input_bytes": len(serialized.encode()),
        "request_bytes": len(canonical(request).encode()),
        "input_sha256": digest(content),
        "reserved_output_tokens": reserved,
        "max_sequence_tokens": settings["max_sequence_tokens"],
        "within_budget": estimate + reserved <= settings["max_sequence_tokens"],
        "truncated": False,
        "usage_source_of_truth": "provider response usage",
    }
