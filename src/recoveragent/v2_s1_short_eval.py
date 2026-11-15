"""User-requested vLLM comparison of already accepted S0 and short S1 weights."""

import argparse
from pathlib import Path

from .demonstrations import sha256
from .io import read_json
from .v2_runtime import S1_SHORT_VLLM_RUNTIME as RUNTIME, S1_SHORT_RUNTIME, verify_lock
from .v2_s1_batch import checkpoint, feedback, initialize
from .v2_tasks import ROOT


def configuration():
    verify_lock(RUNTIME)
    cfg = read_json(ROOT / f"configs/{RUNTIME}.json")
    training = read_json(ROOT / f"configs/{S1_SHORT_RUNTIME}.json")
    fields = (
        "models",
        "selected_model",
        "s0",
        "lora",
        "dev_task_ids",
        "student_limits",
        "answer_format_contract",
        "environment_contract",
        "training_plan_sha256",
    )
    if (
        any(cfg[k] != training[k] for k in fields)
        or cfg["execution_protocol"] != RUNTIME
        or cfg["inference"]["evaluation_backend"] != "vllm"
        or cfg["inference"]["version"] != "0.19.0"
        or sha256(ROOT / training["source_lock"]) != cfg["training_source_lock_sha256"]
    ):
        raise ValueError("vLLM paired evaluation changed the accepted experiment")
    return cfg


def validate_models(deployment, cfg):
    checkpoint(deployment["s0"], cfg, initial=True)
    saved = checkpoint(deployment["s1"], cfg)
    expected = cfg["candidate"]
    if (
        sha256(Path(deployment["s1"]) / "checkpoint_receipt.json")
        != expected["checkpoint_receipt_sha256"]
        or saved["adapter_sha256_after"] != expected["adapter_sha256"]
        or saved["optimizer_steps"] != 40
        or saved["parent_receipt_sha256"] != cfg["s0"]["checkpoint_receipt_sha256"]
    ):
        raise ValueError("vLLM evaluation requires the accepted 40-step candidate")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=["probe-s0", "probe-s1", "baseline", "evaluation"]
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    args = parser.parse_args()
    cfg, deployment = configuration(), read_json(args.deployment)
    validate_models(deployment, cfg)
    output = args.output.resolve()
    initialize(output, cfg, deployment)
    key = "s0" if args.stage in {"probe-s0", "baseline"} else "s1"
    if args.stage.startswith("probe-"):
        from scripts.probe_v2_vllm import probe

        probe(deployment, output / args.stage, cfg=cfg, checkpoint_key=key)
    else:
        feedback(cfg, deployment, output, evaluation=True, evaluation_model=key)


if __name__ == "__main__":
    main()
