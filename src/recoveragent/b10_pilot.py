"""The frozen B10 pilot: existing P3-C, Teacher, deadline server and SFT encoder.

Each worker is isolated. Claims are create-once; no model request is retried.
No training, checkpoint selection, C244 execution or machine provisioning.
"""

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from functools import partial

from . import p3c, p3a, student
from .base_sft import load_tokenizer
from .b10_deadline import session_class
from .demonstrations import sha256
from .full732_c244 import isolated_module
from .full732_recovery_tasks import b_tasks, context_for
from .io import digest, files_manifest, read_json, write_json
from .nullable_recovery_runtime import RUNTIME as SESSION_RUNTIME
from .recovery_suffix import process_attempt
from .student_evaluation import request_json
from .teacher import read_credentials
from .harness import ProtocolHarness, BUDGET_REVISION

ROOT = Path(__file__).resolve().parents[2]
CONFIG = "configs/b10_execution_20261104.json"
LOCK = "configs/b10_execution_20261104_lock.json"
RUNTIME = "b10_m1_teacher_recovery_deadline_20261104"
INITIAL_CONFIG, INITIAL_LOCK, INITIAL_RUNTIME = CONFIG, LOCK, RUNTIME
BUDGET_CONFIG = "configs/b10_execution_budget_20261105.json"
BUDGET_LOCK = "configs/b10_execution_budget_20261105_lock.json"
BUDGET_VARIANT = "m1_b10_deadline_1200_20261105"


def select_configuration(path):
    global CONFIG, LOCK, RUNTIME
    if path == INITIAL_CONFIG:
        CONFIG, LOCK, RUNTIME = INITIAL_CONFIG, INITIAL_LOCK, INITIAL_RUNTIME
    elif path == BUDGET_CONFIG:
        CONFIG, LOCK, RUNTIME = BUDGET_CONFIG, BUDGET_LOCK, BUDGET_REVISION
    else:
        raise ValueError("Unknown B10 execution configuration")


def deadline_contract():
    from . import m1_vgpu_reference_backend as original
    from .b10_deadline import remaining_for_budget

    contract = isolated_module(original, "_b10_deadline_contract")
    if RUNTIME == BUDGET_REVISION:
        contract.VARIANT = BUDGET_VARIANT
        contract.remaining = lambda deadline, now: remaining_for_budget(
            deadline, now, 1200
        )
    return contract


def verify_lock():
    lock = read_json(ROOT / LOCK)
    required = {
        str(p.relative_to(ROOT)) for p in (ROOT / "src/recoveragent").glob("*.py")
    } | {
        CONFIG,
        "scripts/prepare_b10_recovery_pilot.py",
        "configs/b10_recovery_plan_20261104.json",
    }
    if (
        lock.get("runtime_revision") != RUNTIME
        or not required <= lock.get("files", {}).keys()
    ):
        raise ValueError("Incomplete B10 execution lock")
    expected = copy.deepcopy(read_json(ROOT / INITIAL_LOCK)["session_contract"])
    if digest(expected) != student.V6_CONTRACT_SHA256:
        raise ValueError("Original Student contract changed")
    if RUNTIME == BUDGET_REVISION:
        expected["budget_caps"].update(max_requests=80, wall_seconds=1200)
    if lock.get("session_contract") != expected:
        raise ValueError("B10 changed the Student execution contract")
    for name, expected in lock["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or sha256(ROOT / name) != expected
        ):
            raise ValueError("B10 execution source changed: " + name)
    return sha256(ROOT / LOCK)


def configuration():
    verify_lock()
    config = read_json(ROOT / CONFIG)
    plan = read_json(ROOT / config["plan"])
    if (
        config["runtime_revision"] != RUNTIME
        or config["source_lock"] != LOCK
        or sha256(ROOT / config["plan"]) != config["plan_sha256"]
        or plan["rows"] != b_tasks()[1][:10]
        or config["tasks"] != [r["task_id"] for r in plan["rows"]]
    ):
        raise ValueError("Frozen B10 identities/order changed")
    return config


def verify_teacher_config(config):
    expected = configuration()["teacher_config"]
    dynamic = {
        "appworld_root",
        "custom_research",
        "remaining_requests",
        "remaining_generated_tokens",
        "remaining_cost",
    }
    if {k: v for k, v in config.items() if k not in dynamic} != {
        k: v for k, v in expected.items() if k not in dynamic
    }:
        raise ValueError("Teacher recovery protocol/budget differs")
    if config["custom_research"]["task_id"] not in configuration()["tasks"]:
        raise ValueError("Teacher source is outside frozen B10")


def student_config(root, adapter, url):
    config = copy.deepcopy(configuration()["student_config"])
    config.update(appworld_root=root, checkpoint=adapter, inference_url=url)
    return config


def student_engine(config):
    """Versioned isolated bindings, as in existing Full732 engines; new lock enforced."""
    engine = isolated_module(p3c, "_b10_p3c")
    interface = isolated_module(student, "_b10_student")

    def verify_session(revision):
        if revision != SESSION_RUNTIME:
            raise ValueError("Unexpected B10 Student session contract")
        return {
            "revision": revision,
            "lock_sha256": verify_lock(),
            "session_contract": read_json(ROOT / LOCK)["session_contract"],
        }

    def validate(candidate):
        from urllib.parse import urlparse

        fixed = configuration()["student_config"]
        dynamic = {"appworld_root", "checkpoint", "inference_url"}
        if {k: v for k, v in candidate.items() if k not in dynamic} != {
            k: v for k, v in fixed.items() if k not in dynamic
        }:
            raise ValueError("Student protocol changed")
        parsed = urlparse(candidate["inference_url"])
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username
        ):
            raise ValueError(
                "Student backend must be private loopback on the same host"
            )
        return {
            "rows": b_tasks()[1],
            "plan_id": read_json(ROOT / configuration()["plan"])["plan_id"],
        }

    interface.verify_student_runtime = verify_session
    interface.ProtocolHarness = partial(
        ProtocolHarness, budget_revision=config.get("harness_budget_revision")
    )
    engine.validate_config = validate
    engine.verify_source = lambda runtime=SESSION_RUNTIME: verify_session(runtime)[
        "lock_sha256"
    ]
    engine.StudentSession = session_class(
        interface.StudentSession, config["inference_url"], contract=deadline_contract()
    )
    return engine


def selector():
    path = ROOT / "scripts/prepare_b10_recovery_pilot.py"
    spec = importlib.util.spec_from_file_location("_b10_selection", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.checked_plan = lambda: (
        configuration(),
        read_json(ROOT / configuration()["plan"]),
    )
    return module


def verified_m1(path):
    config = configuration()["student_config"]
    p3c.verify_checkpoint(
        path, ROOT / config["checkpoint_release"], config["checkpoint_release_sha256"]
    )
    return read_json(Path(path) / "recoveragent_checkpoint.json")


def serve(args):
    config = configuration()
    metadata = verified_m1(args.adapter)
    if sha256(args.probe) != config["reference_probe_sha256"]:
        raise ValueError("Pinned accepted original-PEFT probe changed")
    backend = deadline_contract()
    # Replace only provenance gates with the new complete source lock and the
    # pinned historical evidence SHA. The tested serving/deadline body is exact.
    backend.verify_lock = verify_lock
    backend.original = SimpleNamespace(verified_checkpoint=verified_m1)

    def validate_probe(receipt, parameter_hash):
        if (
            digest(receipt) != config["reference_probe_canonical_sha256"]
            or receipt["policy"]["checkpoint_id"] != "adapter:" + parameter_hash
        ):
            raise ValueError("Original PEFT memory probe/identity mismatch")

    backend.validate_probe = validate_probe
    args.parameter_hash = metadata["trainable_after_hash"]
    backend.serve(args)


def student_worker(args):
    config = student_config(args.root, args.adapter, args.url)
    if args.task not in configuration()["tasks"]:
        raise ValueError("Task outside frozen B10")
    identity = p3c.verify_checkpoint(
        args.adapter,
        ROOT / config["checkpoint_release"],
        config["checkpoint_release_sha256"],
    )
    backend = request_json(args.url + "/runtime", timeout=10)
    if backend["source_lock_sha256"] != verify_lock() or backend["policy"] != identity:
        raise ValueError("Backend source/identity differs from client")
    row = next(r for r in b_tasks()[1] if r["task_id"] == args.task)
    provenance = {
        "source_lock_sha256": verify_lock(),
        "pilot_runtime": RUNTIME,
        "config_sha256": digest(config),
        "plan_id": read_json(ROOT / configuration()["plan"])["plan_id"],
        "checkpoint_release_sha256": config["checkpoint_release_sha256"],
    }
    result = student_engine(config).run_episode(
        config, row, args.output, provenance, identity
    )
    print(json.dumps({"task_id": args.task, "outcome": result["outcome"]}))


def teacher_worker(args):
    from .state import inspect_snapshot

    source = Path(args.source)
    claim = read_json(args.claim)
    if (
        claim["snapshot_ref"] != str(source.resolve())
        or claim["task_id"] != args.task
        or claim["source_lock_sha256"] != verify_lock()
    ):
        raise ValueError("Teacher worker claim/source differs")
    manifest, _, _ = inspect_snapshot(source)
    if (
        manifest["checkpoint_id"] != claim["checkpoint_id"]
        or manifest["task_id"] != args.task
    ):
        raise ValueError("Teacher checkpoint identity changed")
    write_json(
        Path(args.claim).with_suffix(".started.json"),
        {"pid": os.getpid(), "time": time.time()},
    )
    config = copy.deepcopy(configuration()["teacher_config"])
    config.update(
        appworld_root=args.root,
        custom_research=context_for(args.task),
        remaining_requests=80,
        remaining_generated_tokens=16384,
        remaining_cost=171,
    )
    credentials = read_credentials(args.credentials)
    record = p3a.run_teacher_episode(
        config, args.task, args.output, credentials, recovery_source=source
    )
    write_json(
        Path(args.output) / "execution_provenance.json",
        {
            "claim": claim,
            "config": config,
            "source_lock_sha256": verify_lock(),
            "student_episode_usage": claim["student_episode_usage"],
            "teacher_recovery_usage": record["usage"],
        },
    )
    acceptance = process_attempt(args.output, load_tokenizer())
    print(
        json.dumps(
            {
                "task_id": args.task,
                "status": record["status"],
                "reward": record["reward"],
                "suffix": acceptance["status"],
            }
        )
    )


def child(args, command, output, **fields):
    cmd = [
        sys.executable,
        "-m",
        "recoveragent.b10_pilot",
        command,
        "--config",
        CONFIG,
        "--root",
        args.root,
        "--adapter",
        args.adapter,
        "--url",
        args.url,
        "--output",
        str(output),
    ]
    for name, value in fields.items():
        cmd += ["--" + name.replace("_", "-"), str(value)]
    if command == "teacher":
        cmd += ["--credentials", args.credentials]
    with Path(str(output) + ".log").open("x") as log:
        subprocess.run(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=configuration()["student_config"]["student_limits"]["wall_seconds"]
            + 360,
        )


def summarize(output, stop_reason, prior=None):
    output = Path(output)
    roots = ([Path(prior)] if prior else []) + [output]
    students = [
        read_json(p)
        for root in roots
        for p in sorted((root / "student").glob("*/result.json"))
    ]
    teachers = [
        read_json(p)
        for root in roots
        for p in sorted((root / "teacher").glob("*/trajectory.json"))
    ]
    acceptances = [
        read_json(p)
        for root in roots
        for p in sorted((root / "teacher").glob("*/suffix_acceptance.json"))
    ]
    claims = [p for root in roots for p in (root / "claims").glob("*.json")]
    student_claims = [
        p
        for p in claims
        if p.name.startswith("student_") and not p.name.endswith(".started.json")
    ]
    teacher_claims = [
        p
        for p in claims
        if p.name.startswith("teacher_") and not p.name.endswith(".started.json")
    ]
    return {
        "status": "COMPLETE" if len(students) == 10 and not stop_reason else "BLOCKED",
        "stop_reason": stop_reason,
        "planned_tasks": configuration()["tasks"],
        "student_attempts": len(student_claims),
        "student_results": len(students),
        "natural_failures": sum(r["natural_failure"] for r in students),
        "student_successes": sum(r["outcome"] == "success" for r in students),
        "student_infrastructure_or_unknown": sum(
            r["outcome"] not in {"success", "model_failure"} for r in students
        )
        + len(student_claims)
        - len(students),
        "teacher_attempts": len(teacher_claims),
        "teacher_results": len(teachers),
        "teacher_successes": sum(
            r["status"] == "valid" and r["reward"] == 1 for r in teachers
        ),
        "eligible_suffixes": sum(r["eligible"] for r in acceptances),
        "deferred_suffixes": sum(r["deferred"] for r in acceptances),
        "supervised_tokens": sum(r["supervised_tokens"] for r in acceptances),
        "student_usage": [r["usage"] for r in students],
        "teacher_usage": [r["usage"] for r in teachers],
        "student_outcomes": [
            {k: r.get(k) for k in ("task_id", "outcome", "termination", "fault")}
            for r in students
        ],
        "teacher_outcomes": [
            {
                k: r.get(k)
                for k in (
                    "task_id",
                    "status",
                    "reward",
                    "termination_reason",
                    "error_type",
                )
            }
            for r in teachers
        ],
        "source_lock_sha256": verify_lock(),
        "budget_groups": [
            {
                "source_lock_sha256": sha256(
                    ROOT / read_json(root / "frozen_config.json")["source_lock"]
                ),
                "student_limits": read_json(root / "frozen_config.json")[
                    "student_config"
                ]["student_limits"],
                "student_results": len(list((root / "student").glob("*/result.json"))),
                "teacher_results": len(
                    list((root / "teacher").glob("*/trajectory.json"))
                ),
            }
            for root in roots
        ],
        "training_started": False,
    }


def export_suffixes(output, prior=None):
    from .formal_dataset import write_lines

    directory = Path(output) / "recovery_dataset"
    directory.mkdir(exist_ok=False)
    records, examples, sources = [], [], []
    roots = ([Path(prior)] if prior else []) + [Path(output)]
    paths = [
        p
        for root in roots
        for p in sorted((root / "teacher").glob("*/suffix_acceptance.json"))
    ]
    for path in paths:
        if not read_json(path)["eligible"]:
            continue
        records.append(read_json(path.parent / "recovery_sample.json"))
        examples.extend(read_json(path.parent / "encoded_suffix.json"))
        sources.append(
            {
                "attempt": str(path.parent.resolve()),
                "evidence_manifest_sha256": sha256(
                    path.parent / "evidence_manifest.json"
                ),
            }
        )
    write_lines(directory / "recovery_samples.jsonl", records)
    write_lines(directory / "encoded_examples.jsonl", examples)
    write_json(directory / "sources.json", sources)
    write_json(
        directory / "manifest.json",
        {
            "format": "b10-recovery-suffix-dataset-v1",
            "trajectories": len(records),
            "examples": len(examples),
            "supervised_tokens": sum(sum(e["loss_mask"]) for e in examples),
            "files": files_manifest(directory),
            "source_lock_sha256": verify_lock(),
        },
    )


def prior_evidence(prior):
    """The single authorized budget transition, never a paid-attempt retry."""
    config = configuration()
    if RUNTIME != BUDGET_REVISION or not prior:
        raise ValueError("New budget requires the sealed original B10 stage")
    root = Path(prior).resolve()
    if (
        sha256(root / "pilot_manifest.json")
        != config["continuation"]["prior_manifest_sha256"]
    ):
        raise ValueError("Prior B10 stage identity changed")
    actual = files_manifest(root)
    actual.pop("pilot_manifest.json")
    if actual != read_json(root / "pilot_manifest.json")["files"]:
        raise ValueError("Prior B10 evidence changed")
    if read_json(root / "frozen_config.json") != read_json(ROOT / INITIAL_CONFIG):
        raise ValueError("Prior B10 did not use the original frozen config")
    student_claims = sorted((root / "claims").glob("student_*.json"))
    episodes = {
        p.parent.name: p.parent for p in (root / "student").glob("*/result.json")
    }
    expected = config["tasks"][: config["continuation"]["prior_student_tasks"]]
    if (
        set(episodes) != set(expected)
        or {read_json(p)["task_id"] for p in student_claims} != set(expected)
        or len(student_claims) != len(expected)
    ):
        raise ValueError(
            "Unresolved/duplicate/unexpected Student attempts; never repeat"
        )
    completed = set()
    for path in (root / "claims").glob("teacher_*.json"):
        if path.name.endswith(".started.json"):
            continue
        claim = read_json(path)
        directory = root / "teacher" / path.stem.removeprefix("teacher_")
        if not (directory / "evidence_manifest.json").exists():
            raise ValueError("Unresolved Teacher attempt; never retry")
        key = (claim["task_id"], claim["checkpoint_id"])
        if key in completed:
            raise ValueError("Duplicate prior Teacher attempt")
        completed.add(key)
    if len(completed) != config["continuation"]["prior_teacher_attempts"]:
        raise ValueError("Prior Teacher attempt count differs")
    return episodes, completed


def run(args):
    config = configuration()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ("student", "teacher", "claims", "selections"):
        (output / name).mkdir()
    write_json(output / "frozen_config.json", config)
    write_json(output / "tasks.json", read_json(ROOT / config["plan"]))
    stop_reason, attempts = None, 0
    prior = getattr(args, "prior", None)
    try:
        previous, completed = (
            prior_evidence(prior) if RUNTIME == BUDGET_REVISION else ({}, set())
        )
        attempts = len(completed)
        if prior:
            write_json(
                output / "continuation.json",
                {"prior": str(Path(prior).resolve()), **config["continuation"]},
            )
        identity = p3c.verify_checkpoint(
            args.adapter,
            ROOT / config["student_config"]["checkpoint_release"],
            config["student_config"]["checkpoint_release_sha256"],
        )
        if request_json(args.url, timeout=15) != identity:
            raise ValueError("Live backend is not the released M1")
        for task in config["tasks"]:
            episode = previous.get(task, output / "student" / task)
            if task not in previous:
                write_json(
                    output / "claims" / ("student_" + task + ".json"),
                    {
                        "task_id": task,
                        "source_lock_sha256": verify_lock(),
                        "time": time.time(),
                    },
                )
                child(args, "student", episode, task=task)
            result = read_json(episode / "result.json")
            if result["outcome"] not in {"success", "model_failure"}:
                raise RuntimeError(
                    "Student infrastructure/evaluation failure; no automatic retry"
                )
            selection = selector().select_checkpoints(episode)
            write_json(output / "selections" / (task + ".json"), selection)
            for selected in selection["selected"]:
                if (task, selected["checkpoint_id"]) in completed:
                    continue
                attempts += 1
                if attempts > 20:
                    raise ValueError("Teacher attempt ceiling exceeded")
                key = (
                    task + "_" + selected["kind"] + "_" + selected["checkpoint_id"][:12]
                )
                claim_path = output / "claims" / ("teacher_" + key + ".json")
                claim = {
                    **selected,
                    "task_id": task,
                    "student_episode_usage": result["usage"],
                    "source_lock_sha256": verify_lock(),
                    "time": time.time(),
                }
                write_json(claim_path, claim)
                before = files_manifest(selected["snapshot_ref"])
                try:
                    child(
                        args,
                        "teacher",
                        output / "teacher" / key,
                        task=task,
                        source=selected["snapshot_ref"],
                        claim=claim_path,
                    )
                finally:
                    if files_manifest(selected["snapshot_ref"]) != before:
                        raise ValueError("Immutable Student source was modified")
                teacher = read_json(output / "teacher" / key / "trajectory.json")
                if teacher["status"] != "valid":
                    raise RuntimeError(
                        "Teacher infrastructure/evaluation failure; no paid retry"
                    )
            write_json(output / f"progress_{task}.json", summarize(output, None, prior))
    except Exception as exc:
        stop_reason = type(exc).__name__ + ": " + str(exc)
    export_suffixes(output, prior)
    summary = summarize(output, stop_reason, prior)
    write_json(output / "summary.json", summary)
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "student", "teacher", "run"))
    parser.add_argument(
        "--config", default=INITIAL_CONFIG, choices=(INITIAL_CONFIG, BUDGET_CONFIG)
    )
    parser.add_argument("--prior")
    for name in (
        "root",
        "adapter",
        "probe",
        "output",
        "task",
        "source",
        "claim",
        "credentials",
    ):
        parser.add_argument("--" + name)
    parser.add_argument("--url", default="http://127.0.0.1:8768")
    args = parser.parse_args()
    select_configuration(args.config)
    configuration()
    {"serve": serve, "student": student_worker, "teacher": teacher_worker, "run": run}[
        args.command
    ](args)


if __name__ == "__main__":
    # Helper imports must observe the same explicit CLI selection, not a second
    # module instance silently falling back to the historical configuration.
    sys.modules["recoveragent.b10_pilot"] = sys.modules[__name__]
    main()
