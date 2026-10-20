"""Public metadata audit of real local receipts; never copy task text or credentials."""

import argparse
import collections
from pathlib import Path

from recoveragent.io import digest, files_manifest, read_json, write_json
from recoveragent.teacher import response_usage, usage_cost
from recoveragent.demonstrations import sha256
from recoveragent.teacher_history import validate_history


def audit(runs):
    attempts, totals, identities, tasks = [], collections.Counter(), set(), set()
    for run in map(Path, runs):
        summary = read_json(run / "summary.json")
        totals["sampling_wall_seconds"] += summary["wall_seconds"]
        for path in sorted(run.glob("teacher_*/trajectory.json")):
            trajectory = read_json(path)
            usage = trajectory["usage"]
            actual = collections.Counter()
            finishes, batch_sizes = collections.Counter(), []
            directory = path.parent
            for receipt in sorted((directory / "requests").glob("*.response.json")):
                response = read_json(receipt)
                request = read_json(
                    receipt.with_name(receipt.name.replace(".response.", ".request."))
                )
                validate_history(request["messages"])
                quantities = response_usage(response)
                actual.update(quantities)
                actual["provider_requests"] += 1
                actual["usage_priced_cost"] += usage_cost(
                    quantities, trajectory["teacher"]["pricing"]
                )
                identities.add((response["model"], response["system_fingerprint"]))
                choice = response["choices"][0]
                finishes[choice["finish_reason"]] += 1
                batch_sizes.append(len(choice["message"].get("tool_calls") or []))
            for turn in trajectory["turns"]:
                actual["tool_calls"] += turn.get(
                    "dispatched_count", int(bool(turn.get("dispatched")))
                )
            for key in actual:
                assert abs(actual[key] - usage[key]) < 1e-9, (path.name, key)
                totals[key] += actual[key]
            assert usage["unknown_cost_reservation"] == 0
            assert usage["unknown_generation_reservation"] == 0
            validate_history(trajectory["messages"])
            evaluation = read_json(directory / "evaluation.json")
            assert int(evaluation["success"]) == trajectory["reward"]
            terminal = read_json(directory / "terminal" / "manifest.json")
            assert terminal["checkpoint_id"] == evaluation["terminal_checkpoint_id"]
            assert (
                digest({k: v for k, v in terminal.items() if k != "checkpoint_id"})
                == terminal["checkpoint_id"]
            )
            terminal_files = files_manifest(directory / "terminal")
            terminal_files.pop("manifest.json")
            assert terminal_files == terminal["files"]
            assert (directory / "terminal" / "manifest.json").stat().st_mtime <= (
                directory / "evaluation.json"
            ).stat().st_mtime
            assert terminal["history_hash"] == digest(trajectory["messages"])
            tasks.add(trajectory["task_id"])
            attempts.append(
                {
                    "run_id": run.name,
                    "trajectory_id": trajectory["trajectory_id"],
                    "task_id": trajectory["task_id"],
                    "status": trajectory["status"],
                    "official_reward": trajectory["reward"],
                    "termination_reason": trajectory["termination_reason"],
                    "trajectory_sha256": sha256(path),
                    "evaluation_sha256": sha256(directory / "evaluation.json"),
                    "terminal_checkpoint_id": terminal["checkpoint_id"],
                    "requests": actual["provider_requests"],
                    "tool_calls": actual["tool_calls"],
                    "generated_tokens": actual["generated_tokens"],
                    "finish_reasons": dict(finishes),
                    "max_response_batch_size": max(batch_sizes),
                }
            )
    successes = sum(x["official_reward"] == 1 for x in attempts)
    return {
        "scope": "real Teacher AppWorld train pilot; all protocol retries included",
        "attempts": attempts,
        "unique_attempted_tasks": len(tasks),
        "official_successes": successes,
        "attempt_count": len(attempts),
        "success_per_attempt": successes / len(attempts),
        "observed_backends": sorted([list(x) for x in identities]),
        "totals": dict(totals),
        "cost_currency": "USD",
        "cost_basis": "published peak token rates; upper estimate, not invoice",
        "receipt_usage_and_history_checks": "PASS",
        "terminal_saved_before_evaluation": True,
        "training_started": False,
        "weights_loaded": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit(args.runs)
    write_json(args.output, result)
    print({k: result[k] for k in ("attempt_count", "official_successes", "totals")})


if __name__ == "__main__":
    main()
