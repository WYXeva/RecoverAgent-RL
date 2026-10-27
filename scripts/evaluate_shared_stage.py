"""Prepare/execute/audit the frozen shared-stage protocol. No execution by default."""

import argparse
from pathlib import Path

from recoveragent.io import read_json, write_json
from recoveragent.shared_stage import collect, compare, prepare, verify_lock
from recoveragent.unified_split import sha


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare", "run", "compare"])
    p.add_argument("--protocol", default="configs/shared_stage_validation_v2.json")
    p.add_argument("--plan", default="configs/unified_task_split.json")
    p.add_argument("--lock", default="configs/execution_v6_runtime_lock.json")
    p.add_argument("--stage", choices=["M1", "M2", "M3"])
    p.add_argument("--release")
    p.add_argument("--release-sha256")
    p.add_argument("--inference-url", default="http://127.0.0.1:8766")
    p.add_argument(
        "--stage-run",
        action="append",
        default=[],
        help="M1=/path/to/run; repeat for M2,M3",
    )
    p.add_argument("--output", required=True)
    a = p.parse_args()
    verify_lock(a.lock)
    lock = read_json(a.lock)
    if any(
        sha(path) != lock["files"][key]
        for path, key in (
            (
                a.protocol,
                lock.get("protocol_path", "configs/shared_stage_validation.json"),
            ),
            (a.plan, "configs/unified_task_split.json"),
        )
    ):
        raise ValueError("Use the frozen protocol and split")
    protocol, plan = read_json(a.protocol), read_json(a.plan)
    if a.command in {"prepare", "run"}:
        if not all([a.stage, a.release, a.release_sha256]):
            p.error(
                "A new accepted stage checkpoint release and trusted SHA are required"
            )
        run = prepare(
            protocol, plan, a.stage, a.release, a.release_sha256, a.inference_url
        )
        if a.command == "prepare":
            write_json(a.output, run)
        else:
            collect(run, a.output)
        return
    # Reuse the original raw-receipt accounting; no model or tokenizer needed.
    from scripts.summarize_egtm import summarize

    stage_rows = {}
    for item in a.stage_run:
        stage, folder = item.split("=", 1)
        if stage in stage_rows:
            raise ValueError("Duplicate stage run")
        folder = Path(folder)
        run = read_json(folder / "run.json")
        expected = prepare(
            protocol,
            plan,
            stage,
            run["release_path"],
            run["release_sha256"],
            run["worker_config"]["inference_url"],
        )
        if (
            run != expected
            or read_json(folder / "config.json") != expected["worker_config"]
        ):
            raise ValueError("Unfrozen or altered evaluation config")
        rows = summarize(folder / "config.json", folder)["rows"]
        for row in rows:
            episode = folder / ("base_sft_shared_validation_" + row["task_id"])
            record = (
                read_json(episode / "result.json")
                if (episode / "result.json").exists()
                else {}
            )
            official_path = episode / "official_evaluation.json"
            if record.get("status") == "valid" or official_path.exists():
                from recoveragent.evaluation import classify_official

                official = read_json(official_path)
                classified = classify_official(
                    official.get("official", {}), official.get("observations", [])
                )
                if (classified["status"], classified["success"]) != (
                    record["status"],
                    record["official_success"],
                ):
                    raise ValueError(
                        "Result differs from raw official tracker integrity"
                    )
            schema_errors = 0
            for path in (episode / "session/turns").glob("*.json"):
                event = read_json(path)["event"]
                schema_errors += int(
                    event.get("protocol_rejection") == "invalid_arguments"
                )
            row["schema_errors"] = schema_errors
            row["budget_exhausted"] = record.get("termination") == "budget_exhausted"
        stage_rows[stage] = rows
    coverage = protocol["private_coverage"]
    if sha(coverage["path"]) != coverage["sha256"]:
        raise ValueError("Frozen private coverage annotation changed")
    write_json(
        a.output,
        compare(protocol, plan, stage_rows, read_json(coverage["path"])["rows"]),
    )


if __name__ == "__main__":
    main()
