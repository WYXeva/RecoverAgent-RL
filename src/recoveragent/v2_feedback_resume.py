"""Continue a stopped feedback prefix without replay or changing unknown rewards."""

from pathlib import Path

from .demonstrations import sha256
from .io import digest, files_manifest, read_json
from .v2_resume import carry_clock_interruption, feedback_recipe
from .v2_tasks import ROOT, plan


def resumable_evaluation_error(result):
    evaluation = result.get("evaluation") or {}
    return (
        result.get("classification") == "infrastructure_error"
        and result.get("fault") is None
        and evaluation.get("status") == "evaluation_error"
        and evaluation.get("category") == "unknown"
        and evaluation.get("official_result_complete") is True
        and result.get("success") is None
        and not result.get("usage", {}).get("unknown_generation_reservation")
    )


def carry_feedback(source, cfg, identity, s0, deployment):
    source = Path(source).resolve()
    previous = read_json(source / "run_config.json")
    known = [
        read_json(ROOT / f"configs/v2_round1_{name}_20261109.json")
        for name in ("clock", "feedback")
    ]
    if previous not in known:
        raise ValueError("Unsupported feedback source configuration")
    old = previous
    ignored = {"format", "source_lock"}
    if previous != old or {k: v for k, v in old.items() if k not in ignored} != {
        k: v for k, v in feedback_recipe(cfg, previous).items() if k not in ignored
    }:
        raise ValueError("Feedback continuation changed the frozen recipe")
    preflight = read_json(source / "preflight.json")
    expected_lock = sha256(ROOT / old["source_lock"])
    legacy = Path(
        deployment.get("feedback_source_directories", {}).get(
            preflight["deployment"].get("source_commit"),
            deployment.get("feedback_source_directory", ""),
        )
    ).resolve()
    if (
        preflight["source"]["lock_sha256"] != expected_lock
        or sha256(legacy / old["source_lock"]) != expected_lock
        or deployment["job_deadline_epoch"]
        != preflight["deployment"]["job_deadline_epoch"]
    ):
        raise ValueError("Feedback source/deadline mismatch")
    summary = read_json(source / "round_summary.json")
    if (
        summary["status"] != "stopped"
        or not 1 <= len(summary["feedback"]) <= cfg["feedback_tasks"]
        or any(
            summary.get(k)
            for k in [
                "recovery",
                "generated",
                "evaluations",
                "teacher_usage_priced_cny",
            ]
        )
        or any(
            (source / k).exists()
            for k in ["recovery", "generation", "rollouts", "evaluation"]
        )
    ):
        raise ValueError("Only a completed feedback prefix can be continued")
    if read_json(source / "task_lists.json") != {
        "feedback20": plan()["feedback20"],
        "dev24": plan()["dev24"],
        "split_manifest_sha256": plan()["manifest_sha256"],
    }:
        raise ValueError("Feedback task list changed")
    if read_json(source / "continued_base.json")["checkpoint_receipt_sha256"][
        "S0_base"
    ] != sha256(Path(s0) / "checkpoint_receipt.json"):
        raise ValueError("Feedback S0 changed")
    adapter = read_json(Path(s0) / "checkpoint_receipt.json")["adapter_sha256_after"]
    policy = {**identity, "checkpoint_id": digest({"adapter_sha256": adapter})}
    carried, total, directories = [], 0, []
    prior = source / "continued_feedback.json"
    if prior.exists():
        stored = read_json(prior)
        carried, total, proof = carry_feedback(
            stored["source_run"], cfg, identity, s0, deployment
        )
        if stored != proof:
            raise ValueError("Prior feedback provenance changed")
    prefix_count = len(carried)
    for i, original in enumerate(summary["feedback"]):
        if i < prefix_count:
            if original != carried[i]:
                raise ValueError("Prior carried feedback changed")
            continue
        task = plan()["feedback20"][i]
        directory = Path(original["directory"]).resolve()
        if i == 0 and (source / "carried_clock_interruption.json").exists():
            stored = read_json(source / "carried_clock_interruption.json")
            if stored != original:
                raise ValueError("Previously carried feedback changed")
            verified, tokens, reserved = carry_clock_interruption(
                directory.parents[1], cfg, identity, s0, deployment
            )
            if original != verified or reserved:
                raise ValueError("Clock provenance changed")
            carried.append(original)
            total += tokens
            continue
        expected = source / "feedback" / f"feedback_{i:02d}_{task}"
        if directory != expected or original != {
            **read_json(directory / "result.json"),
            "directory": str(directory),
        }:
            raise ValueError("Feedback result or order differs from the frozen prefix")
        directories.append(directory)
        if (
            original["task_id"] != task
            or original["policy"] != policy
            or (
                original["classification"] not in {"success", "natural_failure"}
                and not resumable_evaluation_error(original)
            )
        ):
            raise ValueError("Unresolved worker or policy failure")
        request_config = read_json(directory.with_name(directory.name + ".config.json"))
        if any(
            request_config[k] != value
            for k, value in {
                "task_id": task,
                "model": identity,
                "checkpoint_id": policy["checkpoint_id"],
                "purpose": "feedback",
                "student_limits": cfg["student_limits"],
                "output": str(directory),
            }.items()
        ):
            raise ValueError("Feedback request configuration changed")
        requests = sorted((directory / "session/requests").glob("*.request.json"))
        responses = list((directory / "session/requests").glob("*.response.json"))
        if (
            not 0
            < len(requests)
            == len(responses)
            <= cfg["student_limits"]["max_requests"]
        ):
            raise ValueError("Unresolved feedback request")
        tokens = 0
        for path in requests:
            request = read_json(path)
            response = read_json(
                path.with_name(path.name.replace(".request.json", ".response.json"))
            )
            if request["policy"] != policy or response["policy"] != policy:
                raise ValueError("Feedback response policy changed")
            tokens += len(response["token_ids"])
        if (
            tokens != original["usage"]["generated_tokens"]
            or original["usage"]["unknown_generation_reservation"]
        ):
            raise ValueError("Feedback cost mismatch")
        total += tokens
        carried.append(
            {
                **original,
                "teacher_source_directory": str(legacy),
                "teacher_source_lock_sha256": expected_lock,
                "retry_performed": False,
            }
        )
    actual = sorted(p for p in (source / "feedback").iterdir() if p.is_dir())
    cancellation = None
    extra = [p for p in actual if p not in directories]
    if extra:
        cancellation = canceled_feedback(source, extra, policy, len(carried))
        total += cancellation["known_generated_tokens"]
    if (
        any(p not in actual for p in directories)
        or total != summary["training_generated_tokens"]
    ):
        raise ValueError(
            "Feedback includes an unfinished task or unaccounted generation"
        )
    return (
        carried,
        total,
        {
            "source_run": str(source),
            "source_summary_sha256": sha256(source / "round_summary.json"),
            "feedback_files": files_manifest(source / "feedback"),
            "source_lock_sha256": expected_lock,
            "completed_tasks": len(carried),
            "generated_tokens": total,
            "no_replay": True,
            "unknown_rewards_preserved": True,
            "job_deadline_epoch": deployment["job_deadline_epoch"],
            **(
                {
                    "cancellation": cancellation,
                    "generation_reservation": cancellation[
                        "unknown_generation_reservation"
                    ],
                }
                if cancellation
                else {}
            ),
        },
    )


def canceled_feedback(source, directories, policy, completed):
    """Account for the request already in flight when the user stopped at15."""
    if not (source / "feedback_limit_interruption.json").exists():
        raise ValueError("Unfinished feedback has no cancellation evidence")
    control = read_json(source / "feedback_limit_interruption.json")
    expected = source / "feedback" / f"feedback_15_{plan()['feedback20'][15]}"
    if (
        completed != 15
        or directories != [expected]
        or control["reason"] != "user_reduce_feedback15"
        or control["feedback_limit"] != 15
        or control["source_summary_sha256"] != sha256(source / "round_summary.json")
        or control["partial_files"] != files_manifest(expected)
        or control["no_retry"] is not True
    ):
        raise ValueError("Unrecognized canceled feedback or changed evidence")
    requests = sorted((expected / "session/requests").glob("*.request.json"))
    known, reserved, unanswered = 0, 0, 0
    for path in requests:
        request = read_json(path)
        if request["policy"] != policy:
            raise ValueError("Canceled request policy changed")
        response_path = path.with_name(
            path.name.replace(".request.json", ".response.json")
        )
        response = read_json(response_path) if response_path.exists() else {}
        if "token_ids" in response:
            if response["policy"] != policy:
                raise ValueError("Canceled response policy changed")
            known += len(response["token_ids"])
        else:
            unanswered += 1
            reserved += request["max_new_tokens"]
    if (
        unanswered != 1
        or read_json(expected / "backend_failure.json")["error_type"] != "TimeoutError"
    ):
        raise ValueError("Unverified canceled generation")
    return {
        "directory": str(expected),
        "known_generated_tokens": known,
        "unknown_generation_reservation": reserved,
        "retry_performed": False,
        "training_eligible": False,
        "control_sha256": sha256(source / "feedback_limit_interruption.json"),
    }
