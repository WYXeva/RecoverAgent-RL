"""Read-only provenance audit of every historical Clean Dataset, with unique costs."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from .demonstrations import sha256, validate_demo, verify_dataset
from .io import canonical, digest, read_json, write_json


def discover(root):
    root = Path(root)
    paths = sorted(
        set(root.glob("runs/*/clean_dataset/manifest.json"))
        | set(root.glob("data/*/manifest.json"))
    )
    return [
        p.parent
        for p in paths
        if read_json(p).get("format") == "clean-teacher-normal-v1"
    ]


def scan(paths, task_scenarios, output):
    """Aliases are retained; repeated packages/receipts do not multiply samples/cost."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    sources, samples, origins, attempts, errors = [], {}, defaultdict(list), {}, []
    for path in paths:
        path = Path(path)
        try:
            manifest = verify_dataset(path, set(task_scenarios))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append({"path": str(path), "error_type": type(exc).__name__})
            continue
        source = {
            "path": str(path),
            "manifest_sha256": sha256(path / "manifest.json"),
            "dataset_id": manifest["dataset_id"],
            "counts": manifest["counts"],
        }
        sources.append(source)
        for line in (path / "demonstrations.jsonl").read_text().splitlines():
            sample = json.loads(line)
            validate_demo(sample)
            if task_scenarios[sample["task_id"]] != sample["scenario_id"]:
                raise ValueError(
                    "Scenario identity does not match official train mapping"
                )
            identity = sample["trajectory_id"]
            if identity in samples and digest(samples[identity]) != digest(sample):
                raise ValueError("Conflicting sample identity: " + identity)
            samples[identity] = sample
            origins[identity].append(source)
        for a in read_json(path / "attempts.json"):
            t = read_json(path / a["evidence"])
            identity = t["trajectory_id"]
            entry = {
                "trajectory_id": identity,
                "task_id": t["task_id"],
                "scenario_id": task_scenarios[t["task_id"]],
                "status": t["status"],
                "official_success": t.get("reward") == 1,
                "accepted": a["accepted"],
                "reasons": a["reasons"],
                "usage": t["usage"],
                "trajectory_hash": digest(t),
            }
            if identity in attempts and attempts[identity] != entry:
                raise ValueError("Conflicting attempt identity: " + identity)
            attempts[identity] = entry
    ordered = [samples[k] for k in sorted(samples)]
    with (output / "samples.jsonl").open("x") as stream:
        for sample in ordered:
            stream.write(canonical(sample) + "\n")
    costs = defaultdict(Counter)
    for a in attempts.values():
        usage = a["usage"]
        currency = usage.get("currency", "UNKNOWN")
        for k in (
            "provider_requests",
            "tool_calls",
            "prompt_tokens",
            "cached_prompt_tokens",
            "generated_tokens",
            "usage_priced_cost",
            "unknown_cost_reservation",
            "unknown_generation_reservation",
        ):
            costs[currency][k] += usage.get(k, 0)
    report = {
        "status": "PASS" if not errors else "PARTIAL",
        "sources": sources,
        "source_errors": errors,
        "package_entries": sum(s["counts"]["accepted"] for s in sources),
        "unique_qualified_trajectories": len(ordered),
        "unique_success_tasks": len({s["task_id"] for s in ordered}),
        "success_families": len({s["scenario_id"] for s in ordered}),
        "unique_attempts": len(attempts),
        "costs_by_currency": dict(costs),
        "billed_cost": None,
        "prompt_versions": dict(
            Counter(s.get("actor_prompt_variant", "legacy_v1") for s in ordered)
        ),
        "tool_modes": dict(
            Counter(s.get("tool_mode", "dynamic_discovery") for s in ordered)
        ),
        "family_distribution": dict(Counter(s["scenario_id"] for s in ordered)),
        "samples_sha256": sha256(output / "samples.jsonl"),
        "official_train_pool_sha256": digest(sorted(task_scenarios.items())),
        "training_started": False,
    }
    write_json(output / "inventory.json", report)
    write_json(output / "origins.json", dict(origins))
    write_json(output / "attempts.json", [attempts[k] for k in sorted(attempts)])
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["scan", "measure"])
    p.add_argument("--root", default=".")
    p.add_argument("--appworld-root", default="data/appworld")
    p.add_argument("--input")
    p.add_argument("--output", required=True)
    p.add_argument("--report", required=True)
    a = p.parse_args()
    if a.command == "scan":
        from .sampling import official_pool

        result = scan(discover(a.root), official_pool(a.appworld_root), a.output)
    else:
        from .base_sft import load_tokenizer
        from .sft_quality import audit_samples

        source = Path(a.input)
        inventory = read_json(source / "inventory.json")
        if sha256(source / "samples.jsonl") != inventory["samples_sha256"]:
            raise ValueError("Audited samples changed")
        samples = [
            json.loads(s) for s in (source / "samples.jsonl").read_text().splitlines()
        ]
        result = audit_samples(samples, load_tokenizer(), a.output, 32768)
        result["source_inventory_sha256"] = sha256(source / "inventory.json")
        result["training_started"] = False
        write_json(Path(a.output) / "quality.json", result)
    write_json(a.report, result)
    print(
        {
            k: v
            for k, v in result.items()
            if k
            in {
                "status",
                "unique_qualified_trajectories",
                "unique_success_tasks",
                "success_families",
                "unique_attempts",
                "ready_complete_demonstrations",
                "max_sequence_tokens",
            }
        }
    )


if __name__ == "__main__":
    main()
