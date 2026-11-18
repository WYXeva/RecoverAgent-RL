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
from recoveragent.v2_runtime import S2_APPEND_TEACHER_EXECUTION, S2_RUNTIME, verify_lock


def main():
    from pathlib import Path

    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--appworld-root", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["RECOVERAGENT_S2_EXECUTION"] = S2_APPEND_TEACHER_EXECUTION
    source = verify_lock(S2_RUNTIME)
    spec = read_json("configs/s2_append_docs_probe_20261118.json")
    base = read_json("configs/v2_s2_static_evolve_20261116.json")
    identity = base["models"][base["selected_model"]]
    cfg = configuration(
        spec["task_id"],
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
            "task_id": spec["task_id"],
            "tool_definition_revision": FULL_DOCUMENT_REVISION,
            "episodes": 1,
            "student_rollouts": 0,
            "optimizer_updates": 0,
            "scope": "User requested one Teacher comparison after stopping Student probe",
        },
    )
    result = run_teacher_episode(
        cfg,
        spec["task_id"],
        args.output / "teacher_episode",
        read_credentials(args.credentials),
        append_document_revision=FULL_DOCUMENT_REVISION,
    )
    print(
        {
            k: result.get(k)
            for k in ("status", "reward", "termination_reason", "usage", "error_type")
        }
    )


if __name__ == "__main__":
    main()
