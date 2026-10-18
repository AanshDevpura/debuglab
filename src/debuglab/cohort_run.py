"""One locked, frozen run over an arbitrary selected bug list."""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import sys
from dataclasses import asdict, replace
from pathlib import Path

from debuglab.artifacts import archive, command
from debuglab.cli import fingerprint
from debuglab.config import Config
from debuglab.io import digest, save
from debuglab.model import require_model_credentials
from debuglab.selection import read_bugs
from debuglab.study import execute


def complete(config: Config, bugs: list[str]) -> bool:
    for bug in bugs:
        path = Path(config.output_root) / bug / "result.json"
        if not path.exists():
            return False
        data = json.loads(path.read_text())
        if not data.get("evaluated") or not data.get("pipeline_complete"):
            return False
    return True


def verify_ledger(root: Path) -> None:
    ledger = json.loads((root / "baseline-artifact-hashes.json").read_text())
    if not ledger or any(digest(Path(p).read_bytes()) != h for p, h in ledger.items()):
        raise RuntimeError("Protected baseline evidence changed or ledger empty")


def final_audit(config: Config, baseline: Path | None = None) -> dict:
    bugs = read_bugs(Path(config.bugs_path))
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
            if (
                baseline is not None
                and (folder / name).read_bytes() != (baseline / bug / name).read_bytes()
            ):
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
                - call["parameters"].get(
                    "max_tokens",
                    call["parameters"].get("max_completion_tokens", config.model.max_output_tokens),
                )
                - config.model.safety_margin
            )
            if (
                not prompt.startswith(context.decode() + "\n\nLOCALIZATION HINT:\n")
                or call.get("input_tokens", call.get("input_tokens_exact", 0)) > limit
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
    audit = {
        "bugs": len(bugs),
        "main_calls": calls,
        "repair_attempts": attempts,
        "feedback_guided_repair": False,
        "plausible_full_suite_proofs": proofs,
        "actual_contexts": contexts,
        "disposable_java_files_remaining": 0,
    }

    if baseline is not None:
        audit.update(
            baseline_evidence_identical=True,
            contexts_rebuilt=any(
                row["hash"] != digest((baseline / row["bug"] / "context.txt").read_bytes())
                for row in contexts
            ),
        )
    return audit


def prepare_run(
    config_path: Path,
    *,
    bugs_path: Path | None = None,
    bug: str | None = None,
    output: Path | None = None,
) -> tuple[Config, Path]:
    config = Config.load(config_path)
    bugs = read_bugs(bugs_path or Path(config.bugs_path))
    if bug is not None:
        if bug not in bugs:
            raise ValueError("Bug is not in the selected evaluation set; update the bug list first")
        bugs = [bug]
    root = output or Path(config.output_root).parent
    if (
        not root.resolve().is_relative_to(Path("outputs").resolve())
        or root.resolve() == Path("outputs").resolve()
    ):
        raise ValueError("Use a run-specific directory inside outputs/")
    if output is not None:
        name = root.name + "-" + digest(str(root.resolve()).encode())[:8]
        config = replace(config, output_root=str(root / "runs"), work_root=f"work/{name}/bugs")
    if not Path(config.work_root).resolve().is_relative_to(Path("work").resolve()):
        raise ValueError("Checkouts must be inside work/")
    effective = replace(config, bugs_path=str(root / "bugs.json"))
    saved_config = root / "config.json"
    freeze = root / "run-fingerprint.json"
    if saved_config.exists():
        if Config.load(saved_config) != effective or read_bugs(Path(effective.bugs_path)) != bugs:
            raise RuntimeError("Run configuration or bug selection changed; use a new --output")
        if not freeze.exists() or json.loads(freeze.read_text()) != fingerprint(saved_config):
            raise RuntimeError("Frozen run source/config/selection changed; use a new --output")
        return effective, saved_config
    existing = [p for p in root.glob("*") if p.name != "coordinator.lock"]
    if existing:
        raise RuntimeError("Output directory contains unowned evidence; use a new --output")
    root.mkdir(parents=True, exist_ok=True)
    save(Path(effective.bugs_path), bugs)
    save(saved_config, asdict(effective))
    archive(root / "source-at-start.zip")
    save(freeze, fingerprint(saved_config))
    return effective, saved_config


def coordinate(
    config_path: Path,
    *,
    bugs_path: Path | None = None,
    bug: str | None = None,
    output: Path | None = None,
) -> None:
    settings = Config.load(config_path)
    root = output or Path(settings.output_root).parent
    if (
        not root.resolve().is_relative_to(Path("outputs").resolve())
        or root.resolve() == Path("outputs").resolve()
    ):
        raise ValueError("Use a run-specific directory inside outputs/")
    root.mkdir(parents=True, exist_ok=True)
    with (root / "coordinator.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another coordinator owns this output directory") from error
        config, saved_config = prepare_run(config_path, bugs_path=bugs_path, bug=bug, output=output)
        bugs = read_bugs(Path(config.bugs_path))
        if not complete(config, bugs):
            require_model_credentials(config.model)
            save(root / "coordinator-state.json", {"stage": "running", "selected": len(bugs)})
            execute(saved_config)
        command([sys.executable, "-m", "debuglab_gold.large_report", "--config", str(saved_config)])
        if not complete(config, bugs):
            save(
                root / "coordinator-state.json", {"stage": "needs_attention", "selected": len(bugs)}
            )
            raise RuntimeError(
                "Incomplete bugs remain selected; inspect worker logs and resume the same command"
            )
        save(root / "final-audit.json", final_audit(config))
        archive(root / "source.zip")
        save(root / "coordinator-state.json", {"stage": "complete", "evaluated": len(bugs)})
        print(f"Completed {len(bugs)} bugs. Report: {root / 'REPORT.md'}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one selected evaluation set")
    parser.add_argument("--config", type=Path, default=Path("configs/local.json"))
    parser.add_argument("--bugs", type=Path)
    parser.add_argument("--bug")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    coordinate(args.config, bugs_path=args.bugs, bug=args.bug, output=args.output)


if __name__ == "__main__":
    main()
