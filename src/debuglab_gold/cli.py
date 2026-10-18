"""Sealed evaluation entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from debuglab.config import Config
from debuglab_gold.evaluate import audit_context, evaluate
from debuglab_gold.report import report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["evaluate", "report", "audit"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    config = Config.load(args.config)
    if args.command == "report":
        report(config)
    else:
        if args.artifacts is None:
            raise ValueError("Evaluation requires artifact directory")
        if args.command == "audit":
            audit = audit_context(config, args.artifacts.name, args.artifacts)
            print(audit, flush=True)
            if not audit["passed"]:
                raise RuntimeError("Literal leak rule conflicts with original buggy context")
            return
        result = evaluate(config, args.artifacts.name, args.artifacts)
        print(f"{result['bug']}: recall={result['context_recall']}", flush=True)
        if result["leak_violations"]:
            raise RuntimeError("Literal fix-added-line leak guard failed; see saved result")


if __name__ == "__main__":
    main()
