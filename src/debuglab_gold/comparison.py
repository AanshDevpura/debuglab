"""Compare separately evaluated runs without feeding gold into model prompts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from debuglab.io import digest, save
from debuglab.selection import read_bugs


def compare(
    baseline_root: Path,
    candidate_root: Path,
    bugs: list[str],
    output: Path,
    *,
    allow_context_changes: bool = False,
) -> dict:
    baseline, candidate = [], []
    changed_contexts = []
    for bug in bugs:
        old = json.loads((baseline_root / bug / "result.json").read_text())
        new = json.loads((candidate_root / bug / "result.json").read_text())
        for result in (old, new):
            if not result.get("evaluated") or not result.get("pipeline_complete"):
                raise ValueError(f"Incomplete comparison bug: {bug}")
        if old["failure_hash"] != new["failure_hash"]:
            raise ValueError(f"Comparison changed frozen inputs: {bug}")
        if old["context_hash"] != new["context_hash"]:
            if not allow_context_changes:
                raise ValueError(f"Comparison changed frozen inputs: {bug}")
            changed_contexts.append(bug)
        if allow_context_changes:
            for name in ("failure.json", "spectrum.json", "source-hashes.json", "methods.json"):
                before, after = baseline_root / bug / name, candidate_root / bug / name
                if digest(before.read_bytes()) != digest(after.read_bytes()):
                    raise ValueError(f"Comparison changed baseline evidence: {bug}/{name}")
        baseline.append(old)
        candidate.append(new)
    counts = {}
    for ranker in ("spectrum", "llm", "combined"):
        for level in ("method", "line"):
            for k in (1, 3, 5):
                key = f"{ranker}_{level}_top{k}"
                counts[key] = {
                    name: sum(
                        bool(r["localization"][ranker]["metrics"][level][f"top{k}"])
                        for r in records
                    )
                    for name, records in (("baseline", baseline), ("candidate", candidate))
                }
    successes = {
        name: {
            (r["bug"], condition)
            for r in records
            for condition, attempts in r["repair"].items()
            if any(a["status"] == "PLAUSIBLE" for a in attempts)
        }
        for name, records in (("baseline", baseline), ("candidate", candidate))
    }
    no_regression = all(v["candidate"] >= v["baseline"] for v in counts.values())
    improved = any(v["candidate"] > v["baseline"] for v in counts.values())
    lost = successes["baseline"] - successes["candidate"]
    result = {
        "bugs": bugs,
        "identical_frozen_inputs": not changed_contexts,
        "context_changes_allowed": allow_context_changes,
        "changed_context_bugs": changed_contexts,
        "comparison_scope": "source-context-and-interface"
        if allow_context_changes
        else "frozen-context",
        "localization_counts": counts,
        "plausible_pairs": {k: sorted(v) for k, v in successes.items()},
        "lost_baseline_plausible_pairs": sorted(lost),
        "top_k_no_regression": no_regression,
        "localization_improved": improved,
        "repair_improved": len(successes["candidate"]) > len(successes["baseline"]),
        "retain": not lost
        and no_regression
        and (improved or len(successes["candidate"]) > len(successes["baseline"])),
    }
    save(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--bugs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-context-changes", action="store_true")
    args = parser.parse_args()
    compare(
        args.baseline,
        args.candidate,
        read_bugs(args.bugs),
        args.output,
        allow_context_changes=args.allow_context_changes,
    )


if __name__ == "__main__":
    main()
