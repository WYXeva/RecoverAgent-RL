"""Fixed A244 fresh campaign. Reuses P3-A execution, strict filters and SFT masks.

Private receipts are append-only. Resumption never reissues a claimed paid
attempt. Official benchmark entry points and old datasets remain separate.
"""

import argparse
import copy
import fcntl
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from .adapter import AppWorldAdapter
from .demonstrations import as_demo, filter_attempt, sha256
from .evaluation import environment_check, evaluate_here
from .io import digest, files_manifest, read_json, write_json
from .p3a import interrupted_usage, run_teacher_episode, validate_config
from .protocol_lock import verify_lock
from .research_access import ROOT, SCOPE, a_tasks, authorize_task, context_for
from .runner import now
from .teacher import preflight, read_credentials

DEFAULT = ROOT / "configs/full732_a244_teacher_serial_diagnostics_20261028.json"


def provider_preflight(config, credentials):
    """Do not silently send a relay key to a differently configured provider."""
    result = preflight(config, credentials)
    reasons = list(result["reasons"])
    base = credentials.get("DEEPSEEK_BASE_URL")
    model = credentials.get("DEEPSEEK_MODEL")
    configured = urlsplit(config["teacher"]["base_url"])
    if base:
        selected = urlsplit(base)
        if (
            selected.scheme != configured.scheme
            or selected.netloc != configured.netloc
            or selected.path.rstrip("/") not in {"", configured.path.rstrip("/")}
            or selected.query
            or selected.fragment
        ):
            reasons.append("CREDENTIAL_ENDPOINT_CONFIG_MISMATCH")
    if model and model != config["teacher"]["model"]:
        reasons.append("CREDENTIAL_MODEL_CONFIG_MISMATCH")
    if config["fresh_campaign"].get("provider_access_verified") is not True:
        reasons.append("PROVIDER_ACCESS_NOT_VERIFIED")
    result.update(
        reasons=sorted(set(reasons)), status="BLOCKED" if reasons else "READY_FOR_PILOT"
    )
    return result


def script_helpers(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot_helpers():
    return script_helpers("probe_snapshot_integrity")


class FreshAdapter(AppWorldAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._initial_scoring = snapshot_helpers().state_observation(self.world)

    def export_state(self, state_id, destination):
        helpers = snapshot_helpers()
        before = helpers.state_observation(self.world)
        super().export_state(state_id, destination)
        after = helpers.state_observation(self.world)
        disk = helpers.disk_observation(destination)
        write_json(
            Path(destination).parent / "scoring_source.json",
            {
                "pid": os.getpid(),
                "before_save": before,
                "after_save": after,
                "saved": disk,
                "exported": disk,
                "write_observed": any(
                    n > self._initial_scoring["changes_counts"].get(app, 0)
                    for app, n in before["changes_counts"].items()
                ),
            },
        )


def run_child(
    mode, output, *, config, task=None, source=None, timeout=660, credentials=None
):
    command = [
        sys.executable,
        "-m",
        "recoveragent.fresh_teacher",
        mode,
        "--config",
        str(Path(config).resolve()),
        "--output",
        str(output.resolve()),
    ]
    if task:
        command += ["--task", task]
    if source:
        command += ["--source", str(Path(source).resolve())]
    if credentials:
        command += ["--credentials-file", str(Path(credentials).resolve())]
    with output.with_suffix(".log").open("x") as log:
        subprocess.run(
            command, check=True, timeout=timeout, stdout=log, stderr=subprocess.STDOUT
        )


def evaluate_custom_isolated(task_id, experiment_name, root, directory, timeout=120):
    directory = Path(directory)
    cfg = directory.parent / (directory.name + ".config.json")
    # The config belongs to this exact fresh worker; never use a caller's READY flag.
    config = read_json(cfg)
    if str(Path(config["appworld_root"]).resolve()) != str(Path(root).resolve()):
        raise ValueError("Evaluation root mismatch")
    authorize_task(root, task_id, config["custom_research"])
    destination = directory / "isolated_evaluation"
    run_child(
        "score",
        destination,
        config=cfg,
        task=task_id,
        source=experiment_name,
        timeout=timeout,
    )
    return read_json(destination / "evaluation.json")


def campaign_config(path=DEFAULT):
    config = read_json(path)
    verify_lock(config)
    settings = config["fresh_campaign"]
    plan, rows = a_tasks()
    expected = sorted(rows, key=lambda r: r["task_id"])
    # Predetermined smoke is the first A train ID; all remaining A IDs are sorted.
    smoke = next(r for r in expected if r["official_split"] == "train")
    expected = [smoke] + [r for r in expected if r != smoke]
    if (
        settings["scope"] != SCOPE
        or settings["tasks"] != [r["task_id"] for r in expected]
        or settings["split_manifest_sha256"] != plan["manifest_sha256"]
        or settings["max_attempts_per_task"] != 1
        or settings["batch_size"] != 10
        or settings["total_cost_cap"] != 244 * config["limits"]["episode_cost"]
        or settings["total_requests"] != 244 * config["limits"]["episode_requests"]
        or settings["total_generated_tokens"] != 3997696
    ):
        raise ValueError("Frozen A244 schedule/budget changed")
    return config, expected


def worker_config(config, row, spent):
    worker = copy.deepcopy(config)
    worker["custom_research"] = context_for(row)
    # Diagnosed setup attempts are never erased or given fresh request/token
    # allowances. Their reconciled usage is bound into the new campaign config.
    setup = config["fresh_campaign"].get("reconciled_setup_usage", {})
    task_setup = setup.get(row["task_id"], {})
    spent = {
        key: value + sum(item.get(key, 0) for item in setup.values())
        for key, value in spent.items()
    }
    worker.update(
        remaining_requests=min(
            config["limits"]["episode_requests"] - task_setup.get("requests", 0),
            config["fresh_campaign"]["total_requests"] - spent["requests"],
        ),
        remaining_generated_tokens=min(
            16384 - task_setup.get("outputs", 0),
            config["fresh_campaign"]["total_generated_tokens"] - spent["outputs"],
        ),
        remaining_cost=min(
            config["limits"]["episode_cost"] - task_setup.get("cost", 0),
            config["fresh_campaign"]["total_cost_cap"] - spent["cost"],
        ),
    )
    validate_config(worker)
    return worker


def result_rows(config, rows, output):
    result = []
    for row in rows:
        identity = digest([digest(config), row["task_id"], 1])
        directory = output / "attempts" / ("fresh_" + identity[:20])
        record = {
            **{k: row[k] for k in ("task_id", "scenario_id", "official_split")},
            "attempt_id": identity,
            "attempt_count": 0,
            "status": "NOT_RUN",
            "official_success": None,
            "replay_verified": False,
            "eligible_for_sft": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "model_requests": 0,
            "tool_calls": 0,
            "cost_or_unknown": 0,
            "priced_cost_estimate": 0,
            "unknown_cost_reservation": 0,
            "billed_cost": None,
            "source_hash": None,
            "reasons": [],
        }
        if directory.with_suffix(".claim.json").exists():
            record.update(
                attempt_count=1,
                status="INTERRUPTED",
                cost_or_unknown=None,
                unknown_cost_reservation=config["limits"]["episode_cost"],
                reasons=["claimed_attempt_no_final_receipt"],
            )
            if directory.exists():
                usage = interrupted_usage(directory, config["teacher"])
                record.update(
                    input_tokens=usage["prompt_tokens"],
                    output_tokens=usage["generated_tokens"],
                    model_requests=usage["provider_requests"],
                    tool_calls=usage["tool_calls"],
                    priced_cost_estimate=usage["usage_priced_cost"],
                    # Keep the unaccounted part of the task's full cost reserve.
                    # A missing final receipt never licenses another paid attempt.
                    unknown_cost_reservation=max(
                        0, config["limits"]["episode_cost"] - usage["usage_priced_cost"]
                    ),
                )
        if (directory / "result.json").exists():
            sealed = read_json(directory / "evidence_manifest.json")["files"]
            actual = files_manifest(directory)
            actual.pop("evidence_manifest.json")
            if sealed != actual:
                raise ValueError("Completed attempt evidence changed")
            final = read_json(directory / "result.json")
            if final["attempt_id"] != identity:
                raise ValueError("Result attempt identity changed")
            record.update(final)
        result.append(record)
    return result


def totals(rows):
    return {
        "requests": sum(r["model_requests"] for r in rows),
        "outputs": sum(r["output_tokens"] for r in rows),
        "cost": sum(
            r["priced_cost_estimate"] + r["unknown_cost_reservation"] for r in rows
        ),
    }


def replay(config, task, source, output):
    """Fresh process, same real terminal; no action replay or business operations."""
    helpers = snapshot_helpers()
    trajectory = read_json(source / "trajectory.json")
    terminal = source / "terminal"
    manifest = read_json(terminal / "manifest.json")
    if (
        digest({k: v for k, v in manifest.items() if k != "checkpoint_id"})
        != manifest["checkpoint_id"]
    ):
        raise ValueError("Terminal manifest tampered")
    files = files_manifest(terminal)
    files.pop("manifest.json")
    if (
        files != manifest["files"]
        or trajectory["terminal_checkpoint_id"] != manifest["checkpoint_id"]
    ):
        raise ValueError("Terminal source differs from actual Teacher")
    live = read_json(terminal / "scoring_source.json")
    live["evaluation"] = read_json(source / "isolated_evaluation/evaluation.json")
    live["receipt_sha256"] = sha256(terminal / "scoring_source.json")
    experiment = source.name + "_restore"
    adapter = AppWorldAdapter(
        config["appworld_root"], task, experiment, research=config["custom_research"]
    )
    output.mkdir(exist_ok=False)
    try:
        adapter.import_state(terminal / "dbs", "fresh_terminal")
        restored = {
            "pid": os.getpid(),
            "after_import": helpers.state_observation(adapter.world),
            "live_receipt_sha256": live["receipt_sha256"],
        }
        adapter.save_terminal()
        disk = Path(adapter.world.output_db_home_path_on_disk)
        restored["saved_terminal"] = helpers.disk_observation(disk)
        write_json(output / "boundaries.json", restored)
    finally:
        adapter.close()
    # Same worker config, evaluation in a further independent process.
    score_dir = output / "score"
    run_child(
        "score",
        score_dir,
        config=source.parent / (source.name + ".config.json"),
        task=task,
        source=experiment,
        timeout=120,
    )
    restored["evaluation"] = read_json(score_dir / "evaluation.json")
    restored["evaluation_db_unchanged"] = (
        files_manifest(disk) == restored["saved_terminal"]["files"]
    )
    restored["export_unchanged"] = (
        files_manifest(terminal / "dbs") == live["exported"]["files"]
    )
    restored["snapshot_scoring_integrity"] = helpers.integrity_verdict(live, restored)
    write_json(output / "receipt.json", restored)


def finish_attempt(config, row, directory, identity, *, smoke=False):
    trajectory = read_json(directory / "trajectory.json")
    usage = interrupted_usage(directory, config["teacher"])
    result = {
        "attempt_id": identity,
        "attempt_count": 1,
        "status": "FAILED" if trajectory["status"] == "valid" else "UNKNOWN",
        "official_success": bool(trajectory["reward"])
        if trajectory["status"] == "valid"
        else None,
        "replay_verified": False,
        "eligible_for_sft": False,
        "input_tokens": usage["prompt_tokens"],
        "output_tokens": usage["generated_tokens"],
        "model_requests": usage["provider_requests"],
        "tool_calls": usage["tool_calls"],
        "priced_cost_estimate": usage["usage_priced_cost"],
        "billed_cost": None,
        "unknown_cost_reservation": usage["unknown_cost_reservation"],
        "cost_or_unknown": usage["usage_priced_cost"] if usage["usage_known"] else None,
        "source_hash": sha256(directory / "trajectory.json"),
        "reasons": [trajectory.get("termination_reason", "unknown")],
        "failure_category": trajectory.get("failure_category"),
    }
    if result["official_success"]:
        result["status"] = "SUCCEEDED_UNVERIFIED"
    if trajectory["status"] == "valid" and (result["official_success"] or smoke):
        try:
            run_child(
                "replay",
                directory / "replay",
                config=directory.with_suffix(".config.json"),
                task=row["task_id"],
                source=directory,
                timeout=180,
            )
            restored = read_json(directory / "replay/receipt.json")
            result["replay_verified"] = (
                restored["snapshot_scoring_integrity"]["status"] == "PASS"
            )
        except (subprocess.SubprocessError, OSError, ValueError, KeyError):
            result["replay_verified"] = False
        if not result["replay_verified"]:
            result.update(
                status="REPLAY_UNVERIFIED", reasons=["snapshot_scoring_not_equivalent"]
            )
    if (
        result["official_success"]
        and result["replay_verified"]
        and usage["usage_known"]
    ):
        evaluation = read_json(directory / "evaluation.json")
        reasons = filter_attempt(
            trajectory, evaluation, {row["task_id"]}, config["limits"], directory
        )
        result.update(status="FILTERED" if reasons else "SUCCEEDED", reasons=reasons)
        if not reasons:
            from .base_sft import assistant_examples
            from .student import parse_batch

            record = as_demo(trajectory)
            tokenizer = None
            try:
                with (directory / "tokenizer.log").open("x") as log:
                    tokenizer = script_helpers("probe_egtm").TokenizerPipe(
                        config["tokenizer_python"], log
                    )
                examples = assistant_examples(record, tokenizer)
                for example in examples:
                    target = example["input_ids"][example["target_start"] :]
                    if len(target) > 4096:
                        raise ValueError("Target exceeds 4096")
                    parsed = parse_batch(tokenizer.decode(target), "audit", "stop")
                    expected = record["messages"][example["assistant_index"]]
                    # Parser assigns its own call IDs; names/arguments must be identical.
                    actual_calls = [c["function"] for c in parsed.get("tool_calls", [])]
                    expected_calls = [
                        c["function"] for c in expected.get("tool_calls", [])
                    ]
                    import json

                    def canonical_calls(calls):
                        return [(c["name"], json.loads(c["arguments"])) for c in calls]

                    if canonical_calls(actual_calls) != canonical_calls(expected_calls):
                        raise ValueError("Student parser changes training target")
                record["provenance"] = {
                    "trajectory_sha256": result["source_hash"],
                    "replay_sha256": sha256(directory / "replay/receipt.json"),
                }
                write_json(directory / "candidate.json", record)
                result.update(
                    eligible_for_sft=True,
                    assistant_samples=len(examples),
                    supervised_tokens=sum(sum(e["loss_mask"]) for e in examples),
                    longest_sequence=max(len(e["input_ids"]) for e in examples),
                )
            except ValueError as exc:
                result.update(status="DEFERRED", reasons=[str(exc)])
            finally:
                if tokenizer is not None:
                    tokenizer.close()
    write_json(directory / "result.json", result)
    write_json(
        directory / "evidence_manifest.json", {"files": files_manifest(directory)}
    )
    return result


def collect(config_path, output, credentials_file, max_batches):
    config, rows = campaign_config(config_path)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "campaign.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        anchor = output / "campaign.json"
        if anchor.exists():
            if read_json(anchor) != config:
                raise ValueError("Campaign config changed; cannot resume")
        else:
            write_json(anchor, config)
        check = provider_preflight(config, read_credentials(credentials_file))
        if check["status"] == "BLOCKED":
            raise ValueError("Teacher preflight blocked: " + ",".join(check["reasons"]))
        environment_check()
        started, executed = now(), 0
        records = result_rows(config, rows, output)
        for index, row in enumerate(rows):
            record = records[index]
            if record["attempt_count"]:
                if (
                    record["status"] in {"UNKNOWN", "INTERRUPTED", "REPLAY_UNVERIFIED"}
                    or record["unknown_cost_reservation"]
                ):
                    break
                continue
            if executed >= max_batches * config["fresh_campaign"]["batch_size"]:
                break
            spent = totals(records)
            worker = worker_config(config, row, spent)
            if (
                min(
                    worker["remaining_cost"],
                    worker["remaining_requests"],
                    worker["remaining_generated_tokens"],
                )
                <= 0
            ):
                break
            authorize_task(
                worker["appworld_root"], row["task_id"], worker["custom_research"]
            )
            identity = record["attempt_id"]
            directory = output / "attempts" / ("fresh_" + identity[:20])
            cfg = directory.with_suffix(".config.json")
            if cfg.exists():
                if read_json(cfg) != worker:
                    raise ValueError("Unclaimed worker configuration changed")
            else:
                write_json(cfg, worker)
            write_json(
                directory.with_suffix(".claim.json"),
                {
                    "attempt_id": identity,
                    "task_id": row["task_id"],
                    "index": index,
                    "batch": index // 10,
                    "config_sha256": sha256(cfg),
                    "cost_reservation": worker["remaining_cost"],
                    "automatic_paid_retry": False,
                },
            )
            try:
                run_child(
                    "worker",
                    directory,
                    config=cfg,
                    task=row["task_id"],
                    credentials=credentials_file,
                )
                result = finish_attempt(
                    worker, row, directory, identity, smoke=index == 0
                )
            except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
                # Claims survive interruptions; never automatically reissue paid work.
                write_json(
                    output / f"stop_{index:03d}.json",
                    {"error_type": type(exc).__name__, "task_id": row["task_id"]},
                )
                break
            records = result_rows(config, rows, output)
            executed += 1
            write_json(
                output / "progress" / f"{index:03d}.json",
                {
                    "completed": index + 1,
                    "last": result,
                    "totals": totals(records),
                    "controller_segment_seconds": now() - started,
                },
            )
            print(f"{index+1}/244 {row['task_id']} {result['status']}", flush=True)
            if (
                result["status"] in {"UNKNOWN", "REPLAY_UNVERIFIED"}
                or result["unknown_cost_reservation"]
            ):
                break
        return result_rows(config, rows, output)


def main():
    # Existing tokenizer-only environment/cache; never load model weights or use GPU.
    os.environ.update(
        HF_HOME=str(ROOT / ".cache/huggingface"),
        HF_HUB_OFFLINE="1",
        CUDA_VISIBLE_DEVICES="",
    )
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["collect", "worker", "score", "replay"])
    p.add_argument("--config", type=Path, default=DEFAULT)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--credentials-file")
    p.add_argument("--task")
    p.add_argument("--source")
    p.add_argument("--max-batches", type=int, default=25)
    a = p.parse_args()
    if a.mode == "collect":
        collect(a.config, a.output, a.credentials_file, a.max_batches)
        return
    config = read_json(a.config)
    validate_config(config)
    authorize_task(config["appworld_root"], a.task, config["custom_research"])
    if a.mode == "worker":
        claim = read_json(a.output.with_suffix(".claim.json"))
        if (
            claim["task_id"] != a.task
            or claim["config_sha256"] != sha256(a.config)
            or a.output.name != "fresh_" + claim["attempt_id"][:20]
        ):
            raise ValueError("Worker has no matching durable campaign claim")
        credentials = read_credentials(a.credentials_file)
        if provider_preflight(config, credentials)["status"] == "BLOCKED":
            raise ValueError("Worker provider preflight blocked")
        run_teacher_episode(config, a.task, a.output, credentials)
    elif a.mode == "score":
        from appworld import update_root

        environment_check()
        update_root(str(Path(config["appworld_root"]).resolve()))
        a.output.mkdir(parents=True, exist_ok=False)
        result = evaluate_here(a.task, Path(a.source).name)
        write_json(a.output / "evaluation.json", result)
    else:
        replay(config, a.task, Path(a.source), a.output)


if __name__ == "__main__":
    main()
