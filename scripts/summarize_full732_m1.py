"""Verify returned M1 artifacts and plot training metrics; never infer validation."""

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            h.update(block)
    return h.hexdigest()


def records(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def training_report(root):
    root = Path(root)
    pipeline = read(root / "summary.json")
    if pipeline["status"] != "PASS":
        raise ValueError("M1 pipeline did not pass; preserve its failure report")
    output = {
        "status": "PASS",
        "data_manifest_sha256": pipeline["source_manifest_sha256"],
        "runtime_lock_sha256": pipeline["runtime_lock_sha256"],
        "pipeline_seconds": pipeline["finished_unix"] - pipeline["started_unix"],
        "training_validation": None,
        "stages": {},
        "checkpoint_observations": [
            read(p) for p in sorted((root / "checkpoint_observations").glob("*.json"))
        ],
    }
    for name in ("short_smoke", "long_smoke", "formal"):
        path = root / name
        receipt_path = path / "recoveragent_checkpoint.json"
        receipt = read(receipt_path)
        parent = next(s for s in pipeline["stages"] if s["stage"] == name)
        if sha(receipt_path) != parent["receipt_sha256"]:
            raise ValueError("Stage receipt differs from the driver")
        if (
            receipt["status"] != "PASS"
            or not receipt["parameter_update_and_reload_pass"]
            or receipt["trainable_before_hash"] == receipt["trainable_after_hash"]
            or receipt["trainable_after_hash"] != receipt["restored_trainable_hash"]
        ):
            raise ValueError("M1 update/reload proof failed")
        for file, expected in receipt["files"].items():
            if (
                Path(file).is_absolute()
                or ".." in Path(file).parts
                or sha(path / file) != expected
            ):
                raise ValueError("Returned checkpoint evidence differs: " + file)
        metrics = records(path / "loss_curve.jsonl")
        gradients = records(path / "gradients.jsonl")
        losses = [r for r in metrics if "loss" in r]
        if (
            len(losses) != len(gradients)
            or len(gradients) != receipt["optimizer_steps"]
        ):
            raise ValueError("Missing optimizer-step metrics")
        if any(
            not math.isfinite(r["loss"]) or not math.isfinite(r["grad_norm"])
            for r in losses
        ):
            raise ValueError("Nonfinite training metrics")
        if any(not math.isfinite(r["l2_norm"]) or r["l2_norm"] <= 0 for r in gradients):
            raise ValueError("Invalid gradient health")
        dataloader = read(path / "dataloader.json")
        if dataloader["expected_optimizer_steps"] != receipt["optimizer_steps"]:
            raise ValueError("Actual steps differ from the dataloader")
        telemetry = root / (name + "_gpu.csv")
        with telemetry.open() as stream:
            gpu = list(csv.DictReader(stream))
        output["stages"][name] = {
            **{
                k: v
                for k, v in receipt.items()
                if k not in ("files", "gradient_checks")
            },
            "config": read(path / "run_config.json"),
            "preflight": read(path / "preflight.json"),
            "dataloader": dataloader,
            "checkpoint_files_verified": len(receipt["files"]),
            "receipt_sha256": sha(receipt_path),
            "loss_curve": losses,
            "gradient_checks": gradients,
            "sampled_peak_gpu_MiB": max(float(r["memory_used_MiB"]) for r in gpu),
        }
    return output


def evaluation_report(root):
    root = Path(root)
    summary = read(root / "tasks/summary.json")
    rows = []
    for original in summary["rows"]:
        path = root / "tasks" / original["task_id"]
        row = {
            k: original[k]
            for k in (
                "task_id",
                "scenario_id",
                "split",
                "status",
                "official_success",
                "initial_state_hash",
                "termination",
                "usage",
                "wall_seconds",
                "error_type",
                "evaluation_exception_types",
                "parallel_scheduler",
                "serial_continuation",
                "config_hash",
                "policy",
            )
            if k in original
        }
        if original["status"] in ("valid", "evaluation_error"):
            official = read(path / "official_evaluation.json")
            if (
                official["status"] != row["status"]
                or official["success"] != row["official_success"]
            ):
                raise ValueError("Scoring receipt differs from controller result")
            row["official_evidence_sha256"] = sha(path / "official_evaluation.json")
            terminal = path / "terminal"
            manifest = read(terminal / "manifest.json")
            for file, expected in manifest["files"].items():
                if (
                    Path(file).is_absolute()
                    or ".." in Path(file).parts
                    or sha(terminal / file) != expected
                ):
                    raise ValueError("Evaluation terminal evidence differs: " + file)
            row["terminal_manifest_sha256"] = sha(terminal / "manifest.json")
            row["terminal_files_verified"] = len(manifest["files"])
            row["terminal_logical_state_hash"] = manifest["logical_state_hash"]
            row["terminal_history_hash"] = manifest["history_hash"]
        requests = sorted((path / "session/requests").glob("*.request.json"))
        responses = sorted((path / "session/requests").glob("*.response.json"))
        if requests:
            first = read(requests[0])
            raw = json.dumps(first["input_token_ids"], separators=(",", ":")).encode()
            row["first_input_token_ids_sha256"] = hashlib.sha256(raw).hexdigest()
        row["saved_requests"], row["saved_responses"] = len(requests), len(responses)
        peaks = [read(p)["peak_allocated_bytes"] for p in responses]
        row["peak_inference_allocated_bytes"] = max(peaks, default=None)
        rows.append(row)
    result = {k: v for k, v in summary.items() if k not in ("rows", "unattempted")}
    result.update(
        rows=rows,
        unattempted_task_ids=[r["task_id"] for r in summary["unattempted"]],
        terminations=dict(Counter(r.get("termination", "no_session") for r in rows)),
        policy=read(root / "backend_identity.json"),
        run_started=read(root / "started.json"),
        run_finished=read(root / "summary.json")
        if (root / "summary.json").exists()
        else None,
        tasks_with_saved_model_requests=sum(r["saved_requests"] > 0 for r in rows),
        tasks_with_saved_model_responses=sum(r["saved_responses"] > 0 for r in rows),
        tasks_with_tool_dispatch_attempts=sum(
            r.get("usage", {}).get("tool_calls", 0) > 0 for r in rows
        ),
        totals={
            key: sum(r.get("usage", {}).get(key, 0) for r in rows)
            for key in (
                "model_requests",
                "prompt_tokens",
                "generated_tokens",
                "tool_calls",
                "unknown_generation_reservation",
            )
        },
    )
    return result


def plot(report, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = report["stages"]["formal"]["loss_curve"]
    steps = [r["step"] for r in rows]
    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True, constrained_layout=True)
    for axis, key, label in zip(
        axes,
        ("loss", "grad_norm"),
        ("Training loss", "Gradient norm (before clipping)"),
        strict=True,
    ):
        axis.plot(steps, [r[key] for r in rows], linewidth=1.1, color="#236b91")
        axis.set_ylabel(label)
        axis.grid(alpha=0.2)
    axes[1].set_xlabel("Optimizer step")
    fig.suptitle("Full732 Base-SFT M1: one epoch, training metrics only")
    fig.savefig(output, metadata={"Creator": "RecoverAgent-RL", "Date": None})
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--training-run", required=True)
    p.add_argument("--evaluation-root")
    p.add_argument("--output", required=True)
    p.add_argument("--plot", required=True)
    a = p.parse_args()
    result = training_report(a.training_run)
    if a.evaluation_root:
        result["C244"] = evaluation_report(a.evaluation_root)
    else:
        result["C244"] = None
    Path(a.output).write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )
    plot(result, a.plot)
    print(
        json.dumps(
            {
                "status": result["status"],
                "optimizer_steps": result["stages"]["formal"]["optimizer_steps"],
                "C244_included": result["C244"] is not None,
            }
        )
    )


if __name__ == "__main__":
    main()
