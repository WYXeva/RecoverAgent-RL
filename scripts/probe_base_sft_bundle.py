"""Negative integrity checks on disposable copies of a real private dataset."""

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from recoveragent.demonstrations import sha256
from recoveragent.formal_dataset import verify
from recoveragent.io import canonical, digest, files_manifest, read_json, write_json


def resign(root):
    """Synthetic tampering only; never overwrite a source or its trusted digest."""
    m = read_json(root / "manifest.json")
    files = files_manifest(root)
    files.pop("manifest.json")
    files.pop("manifest.sha256")
    m["files"] = files
    m.pop("dataset_id")
    m["dataset_id"] = digest(m)
    (root / "manifest.json").write_text(canonical(m) + "\n")
    h = sha256(root / "manifest.json")
    (root / "manifest.sha256").write_text(h + "  manifest.json\n")
    return h


def probe(source, expected):
    verify(source, expected)
    cases = {}
    with tempfile.TemporaryDirectory(prefix="base-sft-integrity-") as tmp:
        for case in (
            "file_tamper",
            "trusted_hash_mismatch",
            "label_leak",
            "control_leak",
        ):
            root = Path(tmp) / case
            shutil.copytree(source, root)
            expected_case = expected
            if case == "trusted_hash_mismatch":
                expected_case = "0" * 64
            elif case == "file_tamper":
                with (root / "tokenized/train.jsonl").open("a") as stream:
                    stream.write("{}\n")
            else:
                file = root / (
                    "tokenized/train.jsonl"
                    if case == "label_leak"
                    else "model_input/train.jsonl"
                )
                lines = file.read_text().splitlines()
                row = json.loads(lines[0])
                if case == "label_leak":
                    row["labels"][0] = row["input_ids"][0]
                else:
                    row["messages"][0]["control_only_test"] = True
                lines[0] = canonical(row)
                file.write_text("\n".join(lines) + "\n")
                expected_case = resign(root)
            try:
                verify(root, expected_case)
            except ValueError as exc:
                cases[case] = {"rejected": True, "reason": str(exc)}
            else:
                raise AssertionError("Tampered copy accepted: " + case)
    return {
        "status": "PASS",
        "source_manifest_sha256": expected,
        "original_unchanged": sha256(Path(source) / "manifest.json") == expected,
        "synthetic_tamper_copies_only": True,
        "cases": cases,
        "training_started": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset")
    p.add_argument("--expected-manifest-sha256", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    result = probe(a.dataset, a.expected_manifest_sha256)
    write_json(a.output, result)
    print(result)
