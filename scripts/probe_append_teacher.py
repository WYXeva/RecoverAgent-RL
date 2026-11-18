"""One real Teacher episode with the same fixed-header/full-document semantics.

Reuses the production Teacher/AppWorld loop, native provider tools and official
evaluation. No Student, GPU, training, generation or Judge. Raw evidence is private.
"""

import argparse
import os

from recoveragent.io import write_json, read_json
from recoveragent.p3a import run_teacher_episode
from recoveragent.student_tokens import FULL_DOCUMENT_REVISION
from recoveragent.teacher import read_credentials
from recoveragent.v2_teacher import configuration
from recoveragent.v2_runtime import (
    S2_APPEND_TEACHER_EXECUTION,
    S2_APPEND_TEXT_EXECUTION,
    S2_RUNTIME,
    verify_lock,
)


def main():
    from pathlib import Path

    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--appworld-root", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task-id")
    parser.add_argument("--successful-reference", type=Path)
    parser.add_argument("--text-template", type=Path)
    args = parser.parse_args()
    os.environ["RECOVERAGENT_S2_EXECUTION"] = (
        S2_APPEND_TEXT_EXECUTION if args.text_template else S2_APPEND_TEACHER_EXECUTION
    )
    source = verify_lock(S2_RUNTIME)
    spec = read_json("configs/s2_append_docs_probe_20261118.json")
    task_id = args.task_id or spec["task_id"]
    reference = None
    if args.successful_reference:
        from recoveragent.io import digest

        evaluation = read_json(args.successful_reference / "evaluation.json")
        prior = read_json(args.successful_reference / "trajectory.json")
        if (
            evaluation["task_id"] != task_id
            or evaluation["success"] is not True
            or evaluation["reference"] != "original_task"
            or prior["task_id"] != task_id
            or prior["status"] != "valid"
            or prior["reward"] != 1
        ):
            raise ValueError(
                "Reference must be an official success on this original task"
            )
        reference = {
            "directory": str(args.successful_reference),
            "evaluation_sha256": digest(evaluation),
            "trajectory_sha256": digest(prior),
            "initial_state_hash": prior["initial_state_hash"],
            "control_only": True,
        }
    base = read_json("configs/v2_s2_static_evolve_20261116.json")
    identity = base["models"][base["selected_model"]]
    cfg = configuration(
        task_id,
        identity,
        args.appworld_root.resolve(),
        2.0,
        recovery=False,
        runtime=S2_RUNTIME,
        purpose="s2_solver",
    )
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(
        args.output / "plan.json",
        {
            "source": source,
            "config": cfg,
            "task_id": task_id,
            "previous_success": reference,
            "text_function_template": str(args.text_template)
            if args.text_template
            else None,
            "tool_definition_revision": FULL_DOCUMENT_REVISION,
            "episodes": 1,
            "student_rollouts": 0,
            "optimizer_updates": 0,
            "scope": "User requested one Teacher comparison after stopping Student probe",
        },
    )
    result = run_teacher_episode(
        cfg,
        task_id,
        args.output / "teacher_episode",
        read_credentials(args.credentials),
        append_document_revision=FULL_DOCUMENT_REVISION,
        text_function_template=args.text_template.read_text()
        if args.text_template
        else None,
    )
    print(
        {
            k: result.get(k)
            for k in ("status", "reward", "termination_reason", "usage", "error_type")
        }
    )


if __name__ == "__main__":
    main()
