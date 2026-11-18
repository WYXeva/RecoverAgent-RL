"""Inspect kernels; --cuda-smoke is synthetic and never loads an actor."""

import argparse

from recoveragent.grpo_kernels import inspect_kernels, smoke
from recoveragent.io import write_json

if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cuda-smoke", action="store_true")
    args = parser.parse_args()
    try:
        result = smoke() if args.cuda_smoke else inspect_kernels()
    except Exception as exc:
        write_json(args.output, {"passed": False, "error": str(exc)})
        raise
    write_json(args.output, result)
    print(result)
