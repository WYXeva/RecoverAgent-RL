"""Bind new evaluation/collection configs to a verified fresh SFT run.

Does not train, start a server, or change any historical release/configuration.
Run with the current worker source, after all three sealed training stages pass.
"""

import argparse
from pathlib import Path

from recoveragent.base_sft import MODEL, REVISION, TEMPLATE_SHA256
from recoveragent.demonstrations import sha256
from recoveragent.harness import REFACTOR_REVISION
from recoveragent.io import read_json, write_json
from recoveragent.p3c import RUNTIME, verify_checkpoint


def prepare(training, dataset, appworld_root, output, minimum_gpu_memory_mib=75000):
    training, dataset, output = map(Path, (training, dataset, output))
    summary = read_json(training / "summary.json")
    expected_stages = ["short_smoke", "length_smoke", "formal"]
    if (
        summary["status"] != "PASS"
        or [s["stage"] for s in summary["stages"]] != expected_stages
    ):
        raise ValueError("Short/length/formal training gates must all pass")
    if sha256(dataset / "manifest.json") != summary["manifest_sha256"]:
        raise ValueError("Training and evaluation data manifests differ")
    for stage in summary["stages"]:
        meta = read_json(training / stage["stage"] / "recoveragent_checkpoint.json")
        if (
            stage["status"] != "PASS"
            or stage["optimizer_steps"] < 1
            or meta["status"] != "PASS"
            or (meta["model"], meta["revision"]) != (MODEL, REVISION)
            or meta["dataset_manifest_sha256"] != summary["manifest_sha256"]
            or not meta["parameter_update_and_reload_pass"]
            or not meta["checkpoint_reloaded"]
            or meta["trainable_before_hash"] == meta["trainable_after_hash"]
            or meta["trainable_after_hash"] != meta["restored_trainable_hash"]
        ):
            raise ValueError("Training update/reload evidence mismatch")
    checkpoint = (training / "formal").resolve()
    names = [
        "adapter_config.json",
        "adapter_model.safetensors",
        "recoveragent_checkpoint.json",
        "run_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
    ]
    files = {name: sha256(checkpoint / name) for name in names}
    if files["chat_template.jinja"] != TEMPLATE_SHA256:
        raise ValueError("Checkpoint template changed")
    release = {
        "status": "TRAINING_AND_RELOAD_PASS",
        "model": MODEL,
        "revision": REVISION,
        "thinking": False,
        "chat_template_sha256": TEMPLATE_SHA256,
        "dataset_manifest_sha256": summary["manifest_sha256"],
        "optimizer_steps": summary["stages"][-1]["optimizer_steps"],
        "adapter_tensor_sha256": meta["trainable_after_hash"],
        "files": files,
        "pipeline_summary_sha256": sha256(training / "summary.json"),
        "public_weights_uploaded": False,
    }
    output.mkdir(parents=True, exist_ok=False)
    release_path = (output / "checkpoint_release.json").resolve()
    write_json(release_path, release)
    verify_checkpoint(checkpoint, release_path, sha256(release_path))
    root = Path(__file__).resolve().parents[1]
    config = read_json(root / "configs/p3c_collection_20261025.json")
    config.update(
        runtime_revision=RUNTIME,
        harness_runtime_revision=REFACTOR_REVISION,
        appworld_root=str(Path(appworld_root).resolve()),
        dataset=str(dataset.resolve()),
        checkpoint=str(checkpoint),
        checkpoint_release=str(release_path),
        checkpoint_release_sha256=sha256(release_path),
        minimum_gpu_memory_mib=minimum_gpu_memory_mib,
    )
    for key in ("family_split", "task_manifest"):
        config[key] = str((root / config[key]).resolve())
    write_json(output / "p3c_config.json", config)
    paired = read_json(root / "configs/p3b_paired_evaluation_20261023.json")
    paired.update(
        format="p3b-shared-execution-evaluation-20261026",
        appworld_root=config["appworld_root"],
        harness_runtime_revision=REFACTOR_REVISION,
        checkpoint_release_sha256=sha256(release_path),
        selection="Historical fixed six-task list; previously inspected development evaluation, not an untouched test set",
    )
    write_json(output / "paired_config.json", paired)
    return release


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--training", required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--appworld-root", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--minimum-gpu-memory-mib", type=int, default=75000)
    a = p.parse_args()
    prepare(a.training, a.dataset, a.appworld_root, a.output, a.minimum_gpu_memory_mib)


if __name__ == "__main__":
    main()
