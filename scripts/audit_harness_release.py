"""Bind new replay receipts to the immutable v1 source and original public inputs."""

import argparse
import json
from pathlib import Path

from recoveragent.demonstrations import sha256
from recoveragent.io import files_manifest, read_json, write_json


def audit(dataset, revalidation):
    dataset, revalidation = Path(dataset), Path(revalidation)
    release = read_json("reports/base_sft_dataset_v1_release.json")
    original = read_json(dataset / "manifest.json")
    if sha256(dataset / "manifest.json") != release["manifest_sha256"]:
        raise ValueError("Original dataset manifest changed")
    if sha256(dataset.with_suffix(".tar.gz")) != release["archive_sha256"]:
        raise ValueError("Original dataset archive changed")
    actual = files_manifest(dataset)
    actual.pop("manifest.json")
    actual.pop("manifest.sha256")
    if actual != original["files"]:
        raise ValueError("Original dataset files changed")
    samples = {
        s["trajectory_id"]: s
        for s in (
            json.loads(line)
            for line in (dataset / "control/all_samples.jsonl").read_text().splitlines()
        )
    }
    selected = [
        json.loads(line)["trajectory_id"]
        for partition in ("train", "validation")
        for line in (dataset / f"model_input/{partition}.jsonl")
        .read_text()
        .splitlines()
    ]
    summary = read_json(revalidation / "summary.json")
    plan = read_json(revalidation / "plan.json")
    if [r["trajectory_id"] for r in plan["rows"]] != selected:
        raise ValueError("Replay selection differs from immutable v1")
    if [r["trajectory_id"] for r in summary["rows"]] != selected:
        raise ValueError("Missing or reordered replay evidence")
    if plan["dataset_manifest_sha256"] != sha256(dataset / "manifest.json"):
        raise ValueError("Replay source dataset mismatch")
    rows = []
    for expected, receipt in zip(plan["rows"], summary["rows"]):
        name = expected["trajectory_id"]
        source_sha = samples[name]["provenance"]["trajectory_sha256"]
        if (
            expected["source_sha256"] != source_sha
            or sha256(expected["source"]) != source_sha
        ):
            raise ValueError("Replay source is not the originally accepted Teacher")
        if (
            receipt != read_json(revalidation / name / "receipt.json")
            or receipt["source_sha256"] != source_sha
        ):
            raise ValueError("Replay receipt mismatch")
        if receipt["status"] == "valid":
            evaluation = read_json(revalidation / name / "isolated_evaluation.json")
            if (
                evaluation["status"] != "valid"
                or evaluation["success"] != receipt["official_success"]
                or evaluation["task_id"] != receipt["task_id"]
                or evaluation["experiment"] != receipt["namespace"]
            ):
                raise ValueError("Independent official evaluation identity mismatch")
        terminal_manifest = revalidation / name / "terminal_manifest.json"
        terminal_files = files_manifest(revalidation / name / "terminal_dbs")
        if terminal_manifest.exists():
            if read_json(terminal_manifest) != terminal_files:
                raise ValueError("Revalidation terminal snapshot changed")
        else:
            write_json(terminal_manifest, terminal_files)
        rows.append(
            {
                **receipt,
                "receipt_sha256": sha256(revalidation / name / "receipt.json"),
                "terminal_manifest_sha256": sha256(terminal_manifest),
                "evaluation_sha256": sha256(
                    revalidation / name / "isolated_evaluation.json"
                )
                if (revalidation / name / "isolated_evaluation.json").exists()
                else None,
            }
        )
    return {
        "scope": "immutable v1 historical replay in compatible environment; not new Teacher samples",
        "original_dataset_unchanged": True,
        "original_files_verified": len(actual),
        "manifest_sha256": sha256(dataset / "manifest.json"),
        "archive_sha256": sha256(dataset.with_suffix(".tar.gz")),
        "plan_sha256": sha256(revalidation / "plan.json"),
        "summary_sha256": sha256(revalidation / "summary.json"),
        "attempts": len(rows),
        "unique_tasks": len({r["task_id"] for r in rows}),
        "unique_scenarios": len(
            {samples[r["trajectory_id"]]["scenario_id"] for r in rows}
        ),
        "official_successes": sum(r.get("official_success") is True for r in rows),
        "initial_states_match": sum(
            r.get("initial_state_matches") is True for r in rows
        ),
        "all_observations_match": sum(
            r.get("observations_match") is True for r in rows
        ),
        "snapshot_roundtrips_match": sum(
            r.get("snapshot_roundtrip_matches") is True for r in rows
        ),
        "nonempty_model_hash_files": sum(
            r.get("model_hash_entries", 0) > 0 for r in rows
        ),
        "real_tool_calls": sum(r["real_tool_calls"] for r in rows),
        "new_teacher_demonstrations": 0,
        "model_requests": 0,
        "training_steps": 0,
        "wall_seconds": summary["wall_seconds"],
        "rows": rows,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", default="data/base_sft_v1")
    p.add_argument("--revalidation", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    result = audit(a.dataset, a.revalidation)
    write_json(a.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))


if __name__ == "__main__":
    main()
