"""Verify a private success bundle against a separately trusted manifest SHA.

Optional re-tokenization uses only cached tokenizer files, no GPU or model.
Original AppWorld databases/provider requests are referenced, not needed here.
"""

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path

from recoveragent.base_sft import MODEL, REVISION, TEMPLATE_SHA256, load_tokenizer
from recoveragent.demonstrations import sha256, validate_demo
from recoveragent.io import digest, files_manifest, read_json
from recoveragent.teacher_inventory import FORMAT, encode


def verify(dataset, expected_sha256, retokenize=False):
    dataset = Path(dataset)
    if sha256(dataset / "manifest.json") != expected_sha256:
        raise ValueError("Trusted manifest SHA differs")
    manifest = read_json(dataset / "manifest.json")
    actual = files_manifest(dataset)
    actual.pop("manifest.json")
    actual.pop("manifest.sha256")
    if actual != manifest["files"]:
        raise ValueError("Bundle file checksum differs")
    if manifest["format"] != FORMAT or manifest["student"] != {
        "model": MODEL,
        "revision": REVISION,
        "thinking": False,
        "template_sha256": TEMPLATE_SHA256,
    }:
        raise ValueError("Dataset or tokenizer protocol differs")
    for name, expected in manifest["source_files"].items():
        if sha256(Path(__file__).parents[1] / "src/recoveragent" / name) != expected:
            raise ValueError("Use the matching source commit")
    rows = read_json(dataset / "inventory.json")
    records = [
        json.loads(line)
        for line in (dataset / "successes.jsonl").read_text().splitlines()
    ]
    if len(records) != len(rows) or len({r["task_id"] for r in records}) != len(
        records
    ):
        raise ValueError("Success coverage/order differs")
    tokenizer = load_tokenizer() if retokenize else None
    counts = Counter()
    with gzip.open(dataset / "assistant_examples.jsonl.gz", "rt") as encoded:
        for record, row in zip(records, rows, strict=True):
            validate_demo(record)
            if (
                row["source_demo_sha256"] != digest(record)
                or row["task_id"] != record["task_id"]
            ):
                raise ValueError("Original demonstration changed")
            if row["disposition"] == "DEFERRED":
                continue
            compact = row["disposition"] == "COMPACT_DERIVED"
            expected_examples = (
                encode(record, tokenizer, compact=compact) if tokenizer else None
            )
            stats = row["compact"] if compact else row["native"]
            for index in range(stats["assistant_samples"]):
                example = json.loads(next(encoded))
                ids, start = example["input_ids"], example["target_start"]
                if (
                    example["task_id"] != record["task_id"]
                    or example["source_demo_sha256"] != digest(record)
                    or example["labels"] != [-100] * start + ids[start:]
                    or example["loss_mask"] != [0] * start + [1] * (len(ids) - start)
                    or example["attention_mask"] != [1] * len(ids)
                    or not 0 < len(ids) - start <= 4096
                    or len(ids) > 32768
                    or (
                        expected_examples is not None
                        and example != expected_examples[index]
                    )
                ):
                    raise ValueError("Encoded context/target/mask differs")
                counts.update(
                    assistant_samples=1,
                    supervised_tokens=len(ids) - start,
                    masked_tokens=start,
                )
            counts["accepted_tokenized_candidates"] += 1
        if next(encoded, None) is not None:
            raise ValueError("Unexpected extra training examples")
    if any(manifest["summary"][key] != value for key, value in counts.items()):
        raise ValueError("Encoded counts differ")
    return {
        "status": "PASS",
        "manifest_sha256": expected_sha256,
        "counts": dict(counts),
        "retokenized": retokenize,
        "model_inference": False,
        "training": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--retokenize", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(verify(args.dataset, args.expected_manifest_sha256, args.retokenize))
    )
