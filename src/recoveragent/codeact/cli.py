"""Thin CLI for the new CodeAct main line."""

import argparse
import json

from recoveragent.io import read_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["collect", "episode", "export", "train", "evaluate"]
    )
    parser.add_argument("--config", default="configs/codeact_reboot_20261119.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--root")
    parser.add_argument("--credentials")
    parser.add_argument("--task")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--collection")
    parser.add_argument("--dataset")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--adapter")
    parser.add_argument("--split", choices=["dev", "train"], default="dev")
    parser.add_argument("--task-set", choices=["all", "dev24"], default="all")
    parser.add_argument("--vllm-url")
    parser.add_argument("--server-receipt")
    parser.add_argument("--target-successes", type=int)
    parser.add_argument("--skip-scoring-errors", action="store_true", default=None)
    args = parser.parse_args()
    config = read_json(args.config)
    if args.command == "collect":
        from .collect import collect

        collect(
            config,
            args.output,
            args.root,
            args.credentials,
            target_successes=args.target_successes,
            skip_scoring_errors=args.skip_scoring_errors,
        )
    elif args.command == "episode":
        from recoveragent.teacher import read_credentials
        from .client import Teacher
        from .episode import run

        result = run(
            args.task,
            args.attempt,
            args.output,
            args.root,
            config,
            Teacher(config, read_credentials(args.credentials)),
        )
        print(
            json.dumps(
                {k: result[k] for k in ("task_id", "success", "valid", "termination")}
            )
        )
    elif args.command == "export":
        from .data import export

        print(json.dumps(export(args.collection, args.output, config)))
    elif args.command == "train":
        from .training import train

        train(config, args.dataset, args.output, smoke=args.smoke)
    else:
        from .inference import evaluate

        evaluate(
            config,
            args.output,
            args.root,
            args.adapter,
            args.split,
            task_set=args.task_set,
            vllm_url=args.vllm_url,
            server_receipt=args.server_receipt,
        )


if __name__ == "__main__":
    main()
