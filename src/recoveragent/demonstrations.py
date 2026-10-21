"""Filter real Teacher evidence; portable, untokenized, train-only datasets."""

import argparse
import copy
import hashlib
import importlib.metadata
import json
import shutil
from pathlib import Path

from jsonschema import ValidationError

from . import APPWORLD_REVISION
from .adapter import call_code
from .io import (
    canonical,
    digest,
    files_manifest,
    read_json,
    write_json,
)
from .teacher import normalize_message, response_usage, usage_cost
from .teacher_history import validate_history
from .validated_selection import validate_predictor_receipts
from .answer_guard import validate_guard_record

FORMAT = "clean-teacher-normal-v1"
FILTER = "official-success-format-loop-v1"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_demo(record):
    """Only the exact structured messages and per-turn public schemas are model input."""
    if (
        record["kind"] != FORMAT
        or record["split"] != "train"
        or record["actor_role"] != "teacher"
    ):
        raise ValueError("Not a normal train Teacher demonstration")
    if record["start_kind"] != "original_initial":
        raise ValueError("Clean demonstration must start at the original initial state")
    messages = record["messages"]
    validate_history(messages)
    if len(messages) < 4 or [m["role"] for m in messages[:2]] != ["system", "user"]:
        raise ValueError("Missing original task context")
    allowed_keys = {
        "system": {"role", "content"},
        "user": {"role", "content"},
        "assistant": {"role", "content", "tool_calls"},
        "tool": {"role", "content", "name", "tool_call_id"},
    }
    for message in messages:
        if set(message) - allowed_keys[message["role"]]:
            raise ValueError("Unexpected control metadata in messages")
        if not isinstance(message.get("content"), str):
            raise ValueError("Expected text content")
    indices = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
    if indices != [t["assistant_index"] for t in record["turns"]]:
        raise ValueError("Assistant turn coverage/order mismatch")
    for turn in record["turns"]:
        index = turn["assistant_index"]
        if digest(messages[:index]) != turn["history_hash"]:
            raise ValueError("Visible history mismatch")
        for tool in turn["tools"]:
            name = tool["function"]["name"]
            if name.startswith("admin__") or "__" not in name:
                raise ValueError("Non-public function in schema")
        calls = messages[index].get("tool_calls", [])
        for offset, call in enumerate(calls):
            function = call["function"]
            call_code(function["name"], function["arguments"], turn["tools"])
            result = messages[index + 1 + offset]
            if result.get("name") != function["name"]:
                raise ValueError("Tool result name mismatch")
    validate_guard_record(record)
    return record


def validate_receipts(trajectory, directory):
    """Recompute usage and verify actual input/output pairing, never trust summary flags."""
    messages = trajectory["messages"]
    totals = {"prompt_tokens": 0, "generated_tokens": 0, "cached_prompt_tokens": 0}
    cost, calls = 0.0, 0
    predictor = validate_predictor_receipts(trajectory, directory)
    validate_guard_record(trajectory)
    if predictor:
        totals.update(predictor["usage"])
        cost += predictor["usage_priced_cost"]
    for turn in trajectory["turns"]:
        request_id = turn["request_id"]
        if not request_id.isdecimal():
            raise ValueError("Invalid receipt identity")
        request = read_json(directory / "requests" / f"{request_id}.request.json")
        response = read_json(directory / "requests" / f"{request_id}.response.json")
        if (
            digest(request) != turn["request_hash"]
            or digest(response) != turn["response_hash"]
        ):
            raise ValueError("Provider receipt changed")
        index = turn["assistant_index"]
        if request["messages"] != messages[:index] or request["tools"] != turn["tools"]:
            raise ValueError("Actual Teacher context mismatch")
        if (
            request["model"] != trajectory["teacher"]["model"]
            or response["model"] != request["model"]
        ):
            raise ValueError("Teacher model identity mismatch")
        if request.get("thinking") != {"type": "disabled"}:
            raise ValueError("Unexpected thinking protocol")
        if (
            normalize_message(
                response, trajectory["teacher"].get("max_tool_calls_per_turn", 1)
            )
            != messages[index]
        ):
            raise ValueError("Teacher output does not match stored Assistant")
        usage = response_usage(response)
        if usage != turn["usage"]:
            raise ValueError("Per-turn usage mismatch")
        for key in totals:
            totals[key] += usage[key]
        cost += usage_cost(usage, trajectory["teacher"]["pricing"])
        if "dispatched_count" in turn:
            count = turn["dispatched_count"]
            if (
                count != len(messages[index].get("tool_calls", []))
                or messages[index + 1 : index + 1 + count] != turn["tool_results"]
            ):
                raise ValueError("Batch observation/count mismatch")
            calls += turn.get("appworld_dispatched_count", count)
        elif turn.get("dispatched"):
            calls += 1
            if messages[index + 1]["content"] != turn["tool_result"]:
                raise ValueError("Tool observation mismatch")
    for key, value in totals.items():
        if value != trajectory["usage"][key]:
            raise ValueError("Total usage mismatch")
    if (
        calls != trajectory["usage"]["tool_calls"]
        or len(trajectory["turns"])
        + (predictor.get("provider_requests", 1) if predictor else 0)
        != trajectory["usage"]["provider_requests"]
        or abs(cost - trajectory["usage"]["usage_priced_cost"]) > 1e-9
    ):
        raise ValueError("Call/cost accounting mismatch")
    if "answer_guard" in trajectory:
        rejections = sum(
            sum(v is not None for v in t["harness_verdicts"])
            for t in trajectory["turns"]
        )
        if (
            trajectory["usage"].get("harness_rejections") != rejections
            or trajectory["usage"].get("tool_requests") != calls + rejections
        ):
            raise ValueError("Harness/real tool accounting mismatch")


def filter_attempt(trajectory, evaluation, train_ids, limits, directory):
    reasons = []
    if (
        trajectory.get("actor_role") != "teacher"
        or trajectory.get("mode") != "teacher_normal"
    ):
        reasons.append("not_teacher_normal")
    teacher = trajectory.get("teacher", {})
    if teacher.get("model") in {
        None,
        "Qwen/Qwen3-1.7B",
        "Qwen/Qwen3-4B",
    } or not teacher.get("revision"):
        reasons.append("unverified_teacher_identity")
    if trajectory.get("task_id") not in train_ids or trajectory.get("split") != "train":
        reasons.append("not_official_train")
    if trajectory.get("status") != "valid":
        reasons.append("infrastructure_error")
    if trajectory.get("start_kind") != "original_initial":
        reasons.append("not_clean_initial")
    if trajectory.get("appworld", {}).get("revision") != APPWORLD_REVISION:
        reasons.append("wrong_appworld_version")
    if (
        not evaluation
        or evaluation.get("success") is not True
        or trajectory.get("reward") != 1
    ):
        reasons.append("official_not_successful")
    if evaluation and (
        evaluation.get("reference") != "original_task"
        or evaluation.get("evaluator") != "appworld.evaluator.evaluate_task"
        or evaluation.get("task_id") != trajectory.get("task_id")
        or evaluation.get("trajectory_id") != trajectory.get("trajectory_id")
        or evaluation.get("terminal_checkpoint_id")
        != trajectory.get("terminal_checkpoint_id")
    ):
        reasons.append("evaluation_identity_mismatch")
    turns = trajectory.get("turns", [])
    if any("format_error" in t or "validation_error" in t for t in turns):
        reasons.append("invalid_call_format")
    if trajectory.get("confirmed_constraint_violation"):
        reasons.append("confirmed_constraint_violation")
    # A repeated identical call AND identical observation is an observable no-progress
    # heuristic, not a hidden safety judgment. Keep the filtering policy versioned.
    streak, previous = 0, None
    for turn in turns:
        index = turn["assistant_index"]
        messages = trajectory.get("messages", [])
        if index >= len(messages):
            continue
        calls = messages[index].get("tool_calls", [])
        for offset, call in enumerate(calls):
            if index + offset + 1 >= len(messages):
                break
            signature = digest(
                [call["function"], messages[index + offset + 1]["content"]]
            )
            streak = streak + 1 if signature == previous else 1
            previous = signature
            if streak >= limits["repeated_identical_call_limit"]:
                reasons.append("repeated_no_progress_loop")
    if not reasons:
        try:
            validate_demo(as_demo(trajectory))
            validate_receipts(trajectory, directory)
        except (
            KeyError,
            ValueError,
            TypeError,
            IndexError,
            OSError,
            ValidationError,
        ) as exc:
            reasons.append("invalid_evidence:" + type(exc).__name__)
    return sorted(set(reasons))


def as_demo(trajectory):
    keys = (
        "trajectory_id",
        "task_id",
        "scenario_id",
        "split",
        "actor_role",
        "start_kind",
        "teacher",
        "appworld",
        "initial_state_hash",
        "messages",
    )
    record = {k: copy.deepcopy(trajectory[k]) for k in keys}
    record["kind"] = FORMAT
    for key in (
        "tool_mode",
        "actor_prompt_variant",
        "predictor_prompt_variant",
        "selection_protocol",
        "answer_guard",
    ):
        if key in trajectory:
            record[key] = trajectory[key]
    record["turns"] = [
        {k: copy.deepcopy(t[k]) for k in ("assistant_index", "history_hash", "tools")}
        for t in trajectory["turns"]
    ]
    if "answer_guard" in trajectory:
        for saved, source in zip(record["turns"], trajectory["turns"]):
            for key in ("harness_verdicts", "supervise", "appworld_dispatched_count"):
                saved[key] = copy.deepcopy(source[key])
    return record


def export_dataset(run, output, train_ids, limits, additional_runs=()):
    """Keep all attempts/receipts in controller evidence; no raw data in public Git."""
    run, output = Path(run), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    attempts, samples, data_versions = [], [], set()
    config = read_json(run / "config.json")
    sources = [run] + [Path(path) for path in additional_runs]
    if len({p.name for p in sources}) != len(sources):
        raise ValueError("Duplicate source run")
    for source in sources:
        write_json(
            output / "source_configs" / (source.name + ".json"),
            read_json(source / "config.json"),
        )
    for path in sorted(
        p for source in sources for p in source.glob("teacher_*/trajectory.json")
    ):
        source = path.parent
        trajectory = read_json(path)
        if trajectory.get("appworld", {}).get("data_version"):
            data_versions.add(trajectory["appworld"]["data_version"])
        if trajectory["trajectory_id"] != source.name:
            raise ValueError("Trajectory directory mismatch")
        evaluation = (
            read_json(source / "evaluation.json")
            if (source / "evaluation.json").exists()
            else None
        )
        reasons = filter_attempt(trajectory, evaluation, train_ids, limits, source)
        evidence = output / "evidence" / source.name
        evidence.mkdir(parents=True)
        shutil.copy2(path, evidence / "trajectory.json")
        if evaluation is not None:
            shutil.copy2(source / "evaluation.json", evidence / "evaluation.json")
        if (source / "requests").is_dir():
            shutil.copytree(source / "requests", evidence / "requests")
        entry = {
            "trajectory_id": source.name,
            "task_id": trajectory["task_id"],
            "status": trajectory["status"],
            "official_success": trajectory.get("reward") == 1,
            "accepted": not reasons,
            "reasons": reasons,
            "usage": trajectory["usage"],
            "evidence": f"evidence/{source.name}/trajectory.json",
        }
        attempts.append(entry)
        if not reasons:
            record = as_demo(trajectory)
            record["provenance"] = {
                "trajectory_sha256": sha256(path),
                "evaluation_sha256": sha256(source / "evaluation.json"),
                "terminal_checkpoint_id": trajectory["terminal_checkpoint_id"],
                "filter_version": FILTER,
            }
            samples.append(record)
    with (output / "demonstrations.jsonl").open("x") as stream:
        for sample in samples:
            stream.write(canonical(sample) + "\n")
    write_json(output / "attempts.json", attempts)
    valid = sum(a["status"] == "valid" for a in attempts)
    successes = sum(a["official_success"] for a in attempts)
    known_fields = (
        "provider_requests",
        "prompt_tokens",
        "generated_tokens",
        "cached_prompt_tokens",
        "tool_calls",
        "usage_priced_cost",
        "unknown_cost_reservation",
        "unknown_generation_reservation",
    )
    if config.get("answer_guard_protocol"):
        known_fields += ("tool_requests", "harness_rejections")
    costs = {k: sum(a["usage"].get(k, 0) for a in attempts) for k in known_fields}
    costs.update(
        billed_cost=None if attempts else 0,
        currency=config["teacher"]["pricing"]["currency"],
        unknown_usage_attempts=sum(
            a["usage"].get("usage_known") is False for a in attempts
        ),
    )
    manifest = {
        "format": FORMAT,
        "filter_version": FILTER,
        "split": "train",
        "appworld_revision": APPWORLD_REVISION,
        "appworld_data_versions": sorted(data_versions),
        "teacher": config["teacher"],
        "target_student": config["student"],
        "protocol": (
            "native_chat_completions_non_thinking_no_call_count_quota"
            if config["limits"]["max_tool_calls"] is None
            and config["teacher"].get("max_tool_calls_per_turn", 1) is None
            else "native_chat_completions_bounded_batch_non_thinking"
        ),
        "source_runs": [
            {"run_id": p.name, "config_sha256": sha256(p / "config.json")}
            for p in sources
        ],
        "dataset_status": "HAS_VERIFIED_SAMPLES"
        if samples
        else "EMPTY_NO_TRAINING_DATA",
        "schema_hashes": sorted(
            {digest(t["tools"]) for s in samples for t in s["turns"]}
        ),
        "training_started": False,
        "counts": {
            "attempted": len(attempts),
            "valid": valid,
            "infrastructure_errors": len(attempts) - valid,
            "official_successes": successes,
            "accepted": len(samples),
            "unique_tasks": len({s["task_id"] for s in samples}),
            "success_per_attempt": successes / len(attempts) if attempts else None,
            "success_per_valid": successes / valid if valid else None,
        },
        "costs": costs,
        "filter_limits": limits,
        "sample_order": [s["trajectory_id"] for s in samples],
        "sample_order_hash": digest([s["trajectory_id"] for s in samples]),
        "license_notice": "Restricted AppWorld-derived data; authorized private transfer only. Do not publish raw data.",
        "files": files_manifest(output),
    }
    manifest["dataset_id"] = digest(manifest)
    write_json(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(
        sha256(output / "manifest.json") + "  manifest.json\n"
    )
    return manifest


def verify_dataset(directory, train_ids, expected_manifest_sha256=None):
    directory = Path(directory)
    actual_manifest = sha256(directory / "manifest.json")
    expected = (
        expected_manifest_sha256
        or (directory / "manifest.sha256").read_text().split()[0]
    )
    if actual_manifest != expected:
        raise ValueError("Dataset manifest SHA-256 mismatch")
    manifest = read_json(directory / "manifest.json")
    body = {k: v for k, v in manifest.items() if k != "dataset_id"}
    if digest(body) != manifest["dataset_id"] or manifest["format"] != FORMAT:
        raise ValueError("Dataset identity/version mismatch")
    if (
        manifest["appworld_revision"] != APPWORLD_REVISION
        or manifest["split"] != "train"
    ):
        raise ValueError("Dataset environment/split mismatch")
    actual_files = files_manifest(directory)
    for name in ("manifest.json", "manifest.sha256"):
        actual_files.pop(name)
    if actual_files != manifest["files"]:
        raise ValueError("Dataset files missing, added or modified")
    for source in manifest.get("source_runs", []):
        run_id = source["run_id"]
        if Path(run_id).name != run_id:
            raise ValueError("Invalid source run ID")
        if (
            sha256(directory / "source_configs" / (run_id + ".json"))
            != source["config_sha256"]
        ):
            raise ValueError("Source config hash differs")
    samples = [
        json.loads(line)
        for line in (directory / "demonstrations.jsonl").read_text().splitlines()
    ]
    order = [s["trajectory_id"] for s in samples]
    if (
        len(set(order)) != len(order)
        or order != manifest["sample_order"]
        or digest(order) != manifest["sample_order_hash"]
        or len(samples) != manifest["counts"]["accepted"]
    ):
        raise ValueError("Sample order/count mismatch")
    attempts = read_json(directory / "attempts.json")
    accepted = []
    for attempt in attempts:
        name = attempt["trajectory_id"]
        if not name.startswith("teacher_") or not name.replace("_", "").isalnum():
            raise ValueError("Invalid evidence directory")
        source = directory / "evidence" / name
        trajectory = read_json(source / "trajectory.json")
        if (
            trajectory["trajectory_id"] != name
            or trajectory["task_id"] != attempt["task_id"]
            or trajectory["status"] != attempt["status"]
            or (trajectory.get("reward") == 1) != attempt["official_success"]
            or trajectory["usage"] != attempt["usage"]
        ):
            raise ValueError("Attempt accounting differs from raw evidence")
        evaluation = (
            read_json(source / "evaluation.json")
            if (source / "evaluation.json").exists()
            else None
        )
        reasons = filter_attempt(
            trajectory, evaluation, train_ids, manifest["filter_limits"], source
        )
        if reasons != attempt["reasons"] or (not reasons) != attempt["accepted"]:
            raise ValueError("Filtering cannot be reproduced")
        if not reasons:
            accepted.append(name)
            record = samples[order.index(name)]
            validate_demo(record)
            if {k: v for k, v in record.items() if k != "provenance"} != as_demo(
                trajectory
            ):
                raise ValueError("Sample differs from original Teacher evidence")
            if (
                sha256(source / "trajectory.json")
                != record["provenance"]["trajectory_sha256"]
                or sha256(source / "evaluation.json")
                != record["provenance"]["evaluation_sha256"]
            ):
                raise ValueError("Sample provenance mismatch")
    if accepted != order or len(attempts) != manifest["counts"]["attempted"]:
        raise ValueError("Attempt denominator mismatch")
    valid = sum(a["status"] == "valid" for a in attempts)
    successes = sum(a["official_success"] for a in attempts)
    expected_counts = {
        "attempted": len(attempts),
        "valid": valid,
        "infrastructure_errors": len(attempts) - valid,
        "official_successes": successes,
        "accepted": len(samples),
        "unique_tasks": len({s["task_id"] for s in samples}),
        "success_per_attempt": successes / len(attempts) if attempts else None,
        "success_per_valid": successes / valid if valid else None,
    }
    if expected_counts != manifest["counts"]:
        raise ValueError("Dataset statistics cannot be reproduced")
    for key in (
        "provider_requests",
        "prompt_tokens",
        "generated_tokens",
        "cached_prompt_tokens",
        "tool_calls",
        "usage_priced_cost",
        "unknown_cost_reservation",
        "unknown_generation_reservation",
        "tool_requests",
        "harness_rejections",
    ):
        if key not in manifest["costs"]:
            continue
        if sum(a["usage"].get(key, 0) for a in attempts) != manifest["costs"][key]:
            raise ValueError("Dataset cost totals cannot be reproduced")
    if (
        sorted({digest(t["tools"]) for s in samples for t in s["turns"]})
        != manifest["schema_hashes"]
    ):
        raise ValueError("Dataset tool schemas differ")
    if any(s["teacher"] != manifest["teacher"] for s in samples):
        raise ValueError("Teacher configuration differs between accepted samples")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["export", "verify"])
    parser.add_argument("path")
    parser.add_argument("--output")
    parser.add_argument("--appworld-root", default="data/appworld")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--include-run", action="append", default=[])
    args = parser.parse_args()
    from appworld import load_task_ids, update_root

    installed = importlib.metadata.distribution("appworld").read_text("direct_url.json")
    if not installed or APPWORLD_REVISION not in installed:
        raise ValueError("Verification requires the pinned AppWorld source")
    update_root(str(Path(args.appworld_root).resolve()))
    train_ids = set(load_task_ids("train"))
    if args.command == "export":
        if not args.output:
            parser.error("--output is required")
        config = read_json(Path(args.path) / "config.json")
        manifest = export_dataset(
            args.path, args.output, train_ids, config["limits"], args.include_run
        )
    else:
        manifest = verify_dataset(args.path, train_ids, args.expected_manifest_sha256)
        data_version = (
            (Path(args.appworld_root) / "data" / "version.txt").read_text().strip()
        )
        if manifest["appworld_data_versions"] and manifest[
            "appworld_data_versions"
        ] != [data_version]:
            raise ValueError("Transferred dataset uses different AppWorld data")
        if args.write_receipt:
            dataset = Path(args.path)
            write_json(
                dataset.parent / (dataset.name + ".verified.json"),
                {
                    "manifest_sha256": sha256(dataset / "manifest.json"),
                    "dataset_id": manifest["dataset_id"],
                    "sample_order_hash": manifest["sample_order_hash"],
                    "official_train_membership_checked": True,
                    "appworld_revision": APPWORLD_REVISION,
                },
            )
    print(
        json.dumps(
            {
                "dataset_id": manifest["dataset_id"],
                "counts": manifest["counts"],
                "manifest_sha256": sha256(
                    Path(args.output or args.path) / "manifest.json"
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
