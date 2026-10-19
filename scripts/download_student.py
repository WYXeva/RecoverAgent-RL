"""Download only the pinned public Student files needed for local inference."""

import argparse
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/p0_p2.json")
    args = parser.parse_args()
    with open(args.config) as stream:
        config = json.load(stream)
    from huggingface_hub import snapshot_download

    print(
        snapshot_download(
            config["model"],
            revision=config["model_revision"],
            allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja"],
            max_workers=2,
        )
    )


if __name__ == "__main__":
    main()
