"""Download the pinned formal 4B tokenizer only, never model weight shards."""

import argparse
import hashlib
from pathlib import Path

from huggingface_hub import snapshot_download

from recoveragent.base_sft import MODEL, REVISION
from recoveragent.io import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    files = [
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
        "config.json",
        "generation_config.json",
    ]
    location = Path(snapshot_download(MODEL, revision=REVISION, allow_patterns=files))
    write_json(
        args.output,
        {
            "model": MODEL,
            "revision": REVISION,
            "weights_downloaded": False,
            "files": {
                name: hashlib.sha256((location / name).read_bytes()).hexdigest()
                for name in files
            },
        },
    )
    print(args.output)


if __name__ == "__main__":
    main()
