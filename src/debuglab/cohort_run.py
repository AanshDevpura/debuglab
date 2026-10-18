"""Frozen one-shot cohort run with rebuilt contexts and protected prior evidence."""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
from pathlib import Path

from debuglab.artifacts import archive, command, publish_reports
from debuglab.cli import fingerprint
from debuglab.config import Config
from debuglab.driver import complete
from debuglab.io import digest, save
from debuglab.replay import reuse


def verify_ledger(root: Path) -> None:
    ledger = json.loads((root / "baseline-artifact-hashes.json").read_text())
    if not ledger or any(digest(Path(p).read_bytes()) != h for p, h in ledger.items()):
        raise RuntimeError("Protected baseline evidence changed or ledger empty")


def final_audit(config: Config, baseline: Path) -> dict:
    bugs = json.loads(Path(config.split_path).read_text())["pool"]
    proofs, contexts = [], []
    calls = attempts = 0
    for bug in bugs:
        folder = Path(config.output_root) / bug
        result = json.loads((folder / "result.json").read_text())
        if not result.get("evaluated") or not result.get("pipeline_complete"):
            raise RuntimeError(f"Incomplete bug: {bug}")
        context = (folder / "context.txt").read_bytes()
        if digest(context) != result["context_hash"] or result["leak_violations"]:
            raise RuntimeError(f"Context or sealed audit mismatch: {bug}")
        for name in ["failure.json", "spectrum.json", "source-hashes.json", "methods.json"]:
            if (folder / name).read_bytes() != (baseline / bug / name).read_bytes():
                raise RuntimeError(f"Baseline input changed: {bug}/{name}")
        if digest((folder / "failure.json").read_bytes()) != result["failure_hash"]:
            raise RuntimeError(f"Failure hash mismatch: {bug}")
        records = list((folder / "calls").glob("*.json"))
        if len(records) != 6:
            raise RuntimeError(f"Expected six one-shot calls: {bug}")
        for path in records:
            call = json.loads(path.read_text())
            prompt = call["parameters"]["messages"][1]["content"]
            limit = (
                call["context_window"]
                - call["parameters"]["max_tokens"]
                - config.model.safety_margin
            )
            if (
                not prompt.startswith(context.decode() + "\n\nLOCALIZATION HINT:\n")
                or call["input_tokens_exact"] > limit
            ):
                raise RuntimeError(f"Actual prompt context/budget failed: {path}")
            if call["status"] == "IN_FLIGHT":
                raise RuntimeError(f"Unfinished call: {path}")
            calls += 1
        if set(result["repair"]) != {"direct", "spectrum", "llm", "combined"}:
            raise RuntimeError(f"Missing repair condition: {bug}")
        for condition, rows in result["repair"].items():
            if len(rows) != 1:
                raise RuntimeError(f"One-shot sample count changed: {bug}/{condition}")
            attempts += 1
            if rows[0]["status"] != "PLAUSIBLE":
                continue
            suites = []
            for path in (folder / "evaluation-logs" / f"{condition}-0-logs").glob("*test.json"):
                record = json.loads(path.read_text())
                if record["command"][-1] == "test" and "-t" not in record["command"]:
                    suites.append((path, record))
            if len(suites) != 1:
                raise RuntimeError(f"Missing or ambiguous full-suite proof: {bug}/{condition}")
            path, record = suites[0]
            failures = re.findall(r"Failing tests:\s*(\d+)", record["output"])
            if record["returncode"] or record["timed_out"] or not failures or failures[-1] != "0":
                raise RuntimeError(f"Plausible patch failed full suite: {path}")
            proofs.append(
                {
                    "bug": bug,
                    "condition": condition,
                    "path": str(path),
                    "sha256": digest(path.read_bytes()),
                }
            )
        contexts.append(
            {
                "bug": bug,
                "hash": result["context_hash"],
                "class_units": len(
                    json.loads((folder / "context-manifest.json").read_text()).get(
                        "source_unit_ids", []
                    )
                ),
            }
        )
    if list(Path(config.work_root).parent.rglob("*.java")):
        raise RuntimeError("Disposable Java checkouts remain")
    return {
        "bugs": len(bugs),
        "main_calls": calls,
        "repair_attempts": attempts,
        "feedback_guided_repair": False,
        "baseline_evidence_identical": True,
        "contexts_rebuilt": any(
            row["hash"] != digest((baseline / row["bug"] / "context.txt").read_bytes())
            for row in contexts
        ),
        "plausible_full_suite_proofs": proofs,
        "actual_contexts": contexts,
        "disposable_java_files_remaining": 0,
    }


def coordinate(
    config_path: Path,
    baseline: Path,
    *,
    rebuild_context: bool = True,
    publish: Path | None = None,
) -> None:
    config = Config.load(config_path)
    if config.samples != 1 or not config.allow_pool_tuning:
        raise ValueError("This coordinator requires one-shot, authorized pooled reporting")
    bugs = json.loads(Path(config.split_path).read_text())["pool"]
    if len(bugs) != 60 or len(set(bugs)) != 60:
        raise ValueError("Expected the original 60 unique selected bugs")
    root = Path(config.output_root).parent
    root.mkdir(parents=True, exist_ok=True)
    with (root / "coordinator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_ledger(root)
        state_path = root / "coordinator-state.json"
        previous = json.loads(state_path.read_text()) if state_path.exists() else {}
        if previous.get("stage") == "complete":
            if not complete(config, bugs) or not (root / "final-audit.json").exists():
                raise RuntimeError("Completed artifacts are incomplete")
            return
        freeze = root / "run-fingerprint.json"
        if freeze.exists():
            if json.loads(freeze.read_text()) != fingerprint(config_path):
                raise RuntimeError("Frozen run code/config/split changed")
        else:
            save(state_path, {"stage": "verification_before_run"})
            with (root / "PRE_RUN_VERIFICATION.txt").open("a") as log:
                subprocess.run(["scripts/verify"], stdout=log, stderr=subprocess.STDOUT, check=True)
            for bug in bugs:
                old = Config.load(baseline / bug / "config.json")
                if old.model != config.model or old.samples != 1:
                    raise ValueError("Baseline model or sample count differs")
                reuse(config, bug, baseline, rebuild_context=rebuild_context)
            archive(root / "source-at-start.zip")
            save(freeze, fingerprint(config_path))
        save(
            state_path,
            {"stage": "running_60", "one_shot": True, "contexts_rebuilt": rebuild_context},
        )
        print("[cohort] running_60, one-shot", flush=True)
        command([sys.executable, "-m", "debuglab.driver", "--config", str(config_path)])
        command([sys.executable, "-m", "debuglab_gold.large_report", "--config", str(config_path)])
        comparison = [
            sys.executable,
            "-m",
            "debuglab_gold.comparison",
            "--baseline",
            str(baseline),
            "--candidate",
            config.output_root,
            "--split",
            config.split_path,
            "--output",
            str(root / "comparison.json"),
        ]
        if rebuild_context:
            comparison.append("--allow-context-changes")
        command(comparison)
        verify_ledger(root)
        save(root / "final-audit.json", final_audit(config, baseline))
        save(state_path, {"stage": "final_verification"})
        with (root / "VERIFICATION.txt").open("w") as log:
            subprocess.run(["scripts/verify"], stdout=log, stderr=subprocess.STDOUT, check=True)
        archive(root / "source.zip")
        if publish is not None:
            publish_reports(root, publish)
        save(
            state_path,
            {"stage": "complete", "evaluated": len(bugs), "report": str(root / "REPORT.md")},
        )
        print("[cohort] complete; pooled report and final audit saved", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--reuse-context", action="store_true", help="Keep baseline contexts exact")
    parser.add_argument("--publish", type=Path, help="Copy compact report artifacts for GitHub")
    args = parser.parse_args()
    coordinate(
        args.config,
        args.baseline,
        rebuild_context=not args.reuse_context,
        publish=args.publish,
    )


if __name__ == "__main__":
    main()
