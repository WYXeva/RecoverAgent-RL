"""CPU-only accounting of exact prefixes in immutable rollout receipts."""

import argparse
from pathlib import Path

from recoveragent.grpo_segments import coalesce
from recoveragent.io import digest, read_json, write_json
from recoveragent.v2_grpo import segments


def audit_rows(rows):
    chains = coalesce(rows)
    old_targets = [
        (token, logprob)
        for row in rows
        for token, logprob in zip(row["completion_ids"], row["logprobs"], strict=True)
    ]
    new_targets = [
        (token, logprob)
        for row in chains
        for token, logprob, mask in zip(
            row["completion_ids"], row["logprobs"], row["tool_mask"], strict=True
        )
        if mask
    ]
    if old_targets != new_targets:
        raise ValueError("Target token or behavior logprob changed")
    return {
        "requests": len(rows),
        "chains": len(chains),
        "original_sequence_tokens": sum(
            len(r["prompt_ids"]) + len(r["completion_ids"]) for r in rows
        ),
        "coalesced_sequence_tokens": sum(
            len(r["prompt_ids"]) + len(r["completion_ids"]) for r in chains
        ),
        "target_tokens": len(old_targets),
        "target_token_logprob_sha256": digest(old_targets),
        "chain_sources": [r["request_refs"] for r in chains],
        "gpu_measured": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--episode", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new audit output; original evidence is immutable")
    results = []
    for directory in args.episode:
        first = min((directory / "session/requests").glob("*.request.json"))
        policy = read_json(first)["policy"]
        results.append(
            {
                "episode": str(directory),
                "policy": policy,
                **audit_rows(segments(directory, policy, max_length=65536)),
            }
        )
    write_json(args.output, {"mode": "cpu_receipt_audit", "episodes": results})
    print({"episodes": len(results), "output": str(args.output), "gpu_measured": False})
