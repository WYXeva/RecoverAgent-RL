"""Upgrade only failed train tasks, then retrain a separate adapter from S0."""

import argparse
import gzip
import json
from pathlib import Path
import subprocess
import time

from . import teacher_followup as followup
from .demonstrations import sha256, validate_demo
from .io import read_json, write_json
from .v2_data import encode, mix_recovery, read_rows, write_rows
from .v2_tasks import ROOT, context_for, rank

REVISION = followup.LOCAL_ESCALATION


def configuration():
    followup.verify_lock(REVISION)
    return followup.configuration(REVISION)


def prior_results(source, cfg):
    """Freeze completed or explicitly drained source work without replaying it."""
    source = Path(source)
    previous = cfg["prior_followup"]
    campaign = read_json(source / "campaign.json")
    status = read_json(source / "progress.json")["status"]
    partial = (
        cfg.get("include_unstarted_after_local_handoff") is True and status == "partial"
    )
    if (
        sha256(source / "config.json") != previous["config_sha256"]
        or campaign["revision"] != previous["revision"]
        or campaign["source_lock_sha256"] != previous["source_lock_sha256"]
        or campaign["task_ids"] != cfg["task_ids"]
        or (status != "complete" and not partial)
    ):
        raise ValueError("Previous Teacher campaign is incomplete or changed")
    values = []
    for task in cfg["task_ids"]:
        path = source / "results" / (task + ".json")
        if path.exists():
            row = read_json(path)
        elif partial:
            matches = sorted((source / "attempts").glob(f"*_{task}_*/result.json"))
            attempts = [read_json(p) for p in matches]
            if len(matches) > 1:
                raise ValueError(
                    "Multiple incomplete source branches require reconciliation"
                )
            path = matches[0] if matches else None
            row = {
                "task_id": task,
                "attempts": attempts,
                "eligible": any(r["eligible"] for r in attempts),
            }
        else:
            raise ValueError("Previous campaign is missing a completed task")
        attempts = row["attempts"]
        names = [r["teacher"] for r in attempts]
        if (
            row["task_id"] != task
            or any(r["task_id"] != task for r in attempts)
            or names
            not in (
                [[], ["deepseek"], ["deepseek", "gpt"]]
                if partial
                else [["deepseek"], ["deepseek", "gpt"]]
            )
            or row["eligible"] != any(r["eligible"] for r in attempts)
            or (not partial and not row["eligible"] and names != ["deepseek", "gpt"])
        ):
            raise ValueError("Previous Teacher task/model attempts changed")
        context_for(task, training=True)
        values.append((path, row))
    return values


def prepare(output, deployment):
    cfg = configuration()
    rows = prior_results(deployment["previous_output"], cfg)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config.json", cfg)
    write_json(output / "deployment.json", deployment)
    write_json(
        output / "campaign.json",
        {
            "revision": REVISION,
            "source_lock_sha256": followup.verify_lock(REVISION),
            "task_ids": [r["task_id"] for _, r in rows if not r["eligible"]],
            "source_results": {
                str(p.resolve()): sha256(p) for p, _ in rows if p is not None
            }
            | {
                str((Path(deployment["previous_output"]) / name).resolve()): sha256(
                    Path(deployment["previous_output"]) / name
                )
                for name in ("progress.json", "campaign.json", "config.json")
            },
            "source_status_sha256": sha256(
                Path(deployment["previous_output"]) / "progress.json"
            ),
            "source_unattempted_tasks": [
                r["task_id"] for _, r in rows if not r["attempts"]
            ],
            "prior_eligible_full_demonstrations": sum(r["eligible"] for _, r in rows),
            "start_kind": "original_initial",
            "new_data_kind": "full_teacher_demonstration_not_recovery_suffix",
            "carried_attempts": {},
        },
    )


def verify_source_results(output, mapping=None):
    for name, expected in read_json(output / "campaign.json")["source_results"].items():
        if sha256(artifact_path(name, mapping or {})) != expected:
            raise ValueError("Frozen previous Teacher results changed")


def artifact_path(value, mapping):
    """Relocate private artifacts; their contents and expected hashes stay fixed."""
    path = Path(value)
    for old, new in sorted(mapping.items(), key=lambda item: -len(item[0])):
        try:
            suffix = path.relative_to(old)
        except ValueError:
            continue
        return Path(new) / suffix
    return path


def select_replay(normal, count):
    """Choose complete trajectories across train scenarios, independent of dev."""
    grouped = {}
    for row in normal:
        context_for(row["task_id"], training=True)
        grouped.setdefault(row["task_id"], []).append(row)
    selected, seen = [], set()
    for task in sorted(grouped, key=rank):
        scenario = context_for(task, training=True)["scenario_id"]
        if scenario in seen:
            continue
        selected.extend(grouped[task])
        seen.add(scenario)
        if len(seen) == count:
            return selected
    raise ValueError("Insufficient independent train scenarios for normal replay")


def fresh_examples(entry, tokenizer, limit, mapping=None):
    directory = artifact_path(entry["encoded_directory"], mapping or {})
    for name, key in (
        ("sample.json", "sample_sha256"),
        ("examples.jsonl.gz", "examples_sha256"),
    ):
        if sha256(directory / name) != entry[key]:
            raise ValueError("Fresh Teacher encoding changed")
    if not entry["official_success"] or entry["recovery_suffix"]:
        raise ValueError("Fresh demonstration provenance is inconsistent")
    sample = read_json(directory / "sample.json")
    validate_demo(sample)
    rows = encode(sample, tokenizer, max_length=limit)
    with gzip.open(directory / "examples.jsonl.gz", "rt") as stream:
        previous = [json.loads(line) for line in stream]
    if rows != previous:
        raise ValueError("Fresh demonstration retokenization changed")
    return [{**r, "teacher_data_kind": "fresh_full"} for r in rows]


def prepare_training(output, deployment):
    from .v2_model import load_tokenizer
    from .v2_training import validate_training_rows

    cfg = configuration()
    verify_source_results(output, deployment.get("artifact_path_map", {}))
    if read_json(output / "progress.json")["status"] != "complete":
        raise ValueError("Upgraded Teacher attempts are incomplete")
    train = cfg["training"]
    if cfg["requested_gpt_upgrade"]["status"] == "unavailable_pending_user_choice":
        raise ValueError(
            "GPT upgrade disposition is unresolved; preserve collected data"
        )
    identity = train["models"][train["selected_model"]]
    tokenizer = load_tokenizer(identity)
    old = Path(deployment["original_training_output"])
    for name, expected in cfg["training_source_files"].items():
        if sha256(old / name) != expected:
            raise ValueError("Original Teacher training data changed: " + name)
    recovery = read_rows(old / "data/recovery_examples.jsonl")
    normal = select_replay(
        read_rows(old / "data/normal/examples.jsonl"), train["normal_replay_tasks"]
    )
    recovery = [{**r, "teacher_data_kind": "recovery_suffix"} for r in recovery]
    normal = [{**r, "teacher_data_kind": "original_replay"} for r in normal]
    fresh, sources, tasks = [], [], set()
    for root in [Path(deployment["previous_output"]), output]:
        for path in sorted((root / "results").glob("*.json")):
            for entry in read_json(path)["attempts"]:
                if not entry["eligible"]:
                    continue
                if entry["task_id"] in tasks:
                    raise ValueError("Duplicate successful fresh task")
                tasks.add(entry["task_id"])
                fresh.extend(
                    fresh_examples(
                        entry,
                        tokenizer,
                        train["student_limits"]["max_input_tokens"],
                        deployment.get("artifact_path_map", {}),
                    )
                )
                sources.append(
                    {"path": str(path), "sha256": sha256(path), "entry": entry}
                )
    if not fresh:
        raise ValueError("No new qualified Teacher demonstrations")
    if tasks & {r["task_id"] for r in recovery + normal}:
        raise ValueError("Fresh and original training sources overlap")
    rows = mix_recovery(
        recovery,
        fresh + normal,
        recovery_fraction=train["recovery_sft"]["recovery_supervised_token_fraction"],
        select_normal=False,
    )
    validate_training_rows(rows, train["student_limits"]["max_input_tokens"])
    target = output / "training_data"
    target.mkdir(exist_ok=False)
    write_rows(target / "examples.jsonl", rows)
    write_json(target / "sources.json", sources)
    write_json(
        target / "manifest.json",
        {
            "revision": REVISION,
            "parent": deployment["s0"],
            "expected_parent": train["s0"],
            "source_lock_sha256": followup.verify_lock(REVISION),
            "examples_sha256": sha256(target / "examples.jsonl"),
            "sources_sha256": sha256(target / "sources.json"),
            "examples": len(rows),
            "max_sequence_tokens": max(len(r["input_ids"]) for r in rows),
            "groups": {
                kind: {
                    "tasks": len(
                        {r["task_id"] for r in rows if r["teacher_data_kind"] == kind}
                    ),
                    "examples": sum(r["teacher_data_kind"] == kind for r in rows),
                    "supervised_tokens": sum(
                        sum(r["loss_mask"])
                        for r in rows
                        if r["teacher_data_kind"] == kind
                    ),
                    "objective_fraction": sum(
                        r["example_weight"]
                        for r in rows
                        if r["teacher_data_kind"] == kind
                    )
                    / len(rows),
                }
                for kind in ("recovery_suffix", "fresh_full", "original_replay")
            },
            "truncated": False,
            "validation_data_used": False,
        },
    )


def train(output, deployment):
    from .v2_s1_batch import checkpoint

    cfg = configuration()["training"]
    parent = checkpoint(deployment["s0"], cfg, initial=True)
    examples = output / "training_data/examples.jsonl"
    manifest = read_json(examples.parent / "manifest.json")
    if (
        sha256(examples) != manifest["examples_sha256"]
        or manifest["revision"] != REVISION
        or manifest["source_lock_sha256"] != followup.verify_lock(REVISION)
        or manifest["expected_parent"] != cfg["s0"]
    ):
        raise ValueError("Frozen retraining examples changed")
    for profile, name in (
        ("short", "smoke_short"),
        ("long", "smoke_long"),
        ("formal", cfg["output_name"]),
    ):
        target = output / name
        if not target.exists():
            request = output / (name + ".input.json")
            write_json(
                request,
                {
                    "config": cfg,
                    "identity": cfg["models"][cfg["selected_model"]],
                    "examples": str(examples),
                    "output": str(target),
                    "parent": deployment["s0"],
                    "profile": profile,
                    "deadline_epoch": deployment["deadline_epoch"],
                },
            )
            with (output / (name + ".log")).open("x") as log:
                subprocess.run(
                    [
                        deployment["gpu_python"],
                        str(ROOT / "scripts/run_teacher_escalation.py"),
                        "train-worker",
                        "--input",
                        str(request),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
        receipt = checkpoint(target, cfg)
        if (
            receipt["adapter_sha256_before"] != parent["adapter_sha256_after"]
            or receipt["profile"] != profile
            or receipt["data_sha256"] != manifest["examples_sha256"]
        ):
            raise ValueError("Retraining did not load the accepted S0/profile")
        accepted = output / (name + "_accepted.json")
        if accepted.exists():
            if read_json(accepted) != receipt:
                raise ValueError("Accepted retraining checkpoint changed")
        else:
            write_json(accepted, receipt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage",
        choices=["prepare", "collect", "prepare-training", "train", "train-worker"],
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--deployment", type=Path)
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    cfg = configuration()
    if args.stage == "train-worker":
        from .v2_training import train_sft

        payload = read_json(args.input)
        if payload.pop("config") != cfg["training"]:
            raise ValueError("Retraining worker config mismatch")
        payload["deadline"] = time.monotonic() + max(
            0, payload.pop("deadline_epoch") - time.time()
        )
        train_sft(cfg["training"], **payload)
        return
    output = args.output.resolve()
    if args.stage == "prepare":
        prepare(output, read_json(args.deployment))
    else:
        deployment = read_json(output / "deployment.json")
        if args.stage in {"prepare-training", "train"} and args.deployment is not None:
            # A new GPU host may use different paths. Keep the collection deployment
            # immutable and record a separate, explicit training deployment.
            deployment = read_json(args.deployment)
            target = output / "training_deployment.json"
            if target.exists():
                if read_json(target) != deployment:
                    raise ValueError("Training deployment changed")
            else:
                write_json(target, deployment)
        verify_source_results(output, deployment.get("artifact_path_map", {}))
        if read_json(output / "config.json") != cfg:
            raise ValueError("Frozen upgraded campaign configuration changed")
        if args.stage == "collect":
            followup.collect(output)
        elif args.stage == "prepare-training":
            prepare_training(output, deployment)
        else:
            train(output, deployment)


if __name__ == "__main__":
    main()
