"""Isolated workers, deadlines and safe cleanup for one selected evaluation set."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from debuglab.config import Config
from debuglab.io import digest, save
from debuglab.selection import read_bugs


class BugDeadline(BaseException):
    """An overall deadline must escape model/attempt exception handlers."""


def cleanup(config: Config, bug: str) -> None:
    """Remove only this study's disposable checkouts, preserving all artifacts."""
    if not config.cleanup_checkouts:
        return
    root = Path(config.work_root).resolve()
    if not root.is_relative_to(Path("work").resolve()):
        raise ValueError("Cleanup requires a checkout root inside workspace work/")
    attempts = root.parent / "attempts" / bug
    if attempts.exists():
        for logs in attempts.glob("*-logs"):
            archive = Path(config.output_root) / bug / "evaluation-logs" / logs.name
            suffix = 0
            while archive.exists():
                old = {
                    p.relative_to(archive).as_posix(): digest(p.read_bytes())
                    for p in archive.rglob("*")
                    if p.is_file()
                }
                new = {
                    p.relative_to(logs).as_posix(): digest(p.read_bytes())
                    for p in logs.rglob("*")
                    if p.is_file()
                }
                if old == new:
                    break
                suffix += 1
                archive = archive.with_name(logs.name + f"-cleanup-{suffix}")
            else:
                shutil.copytree(logs, archive)
    paths = [root / bug, attempts, Path("work/gold") / bug / "fixed"]
    for path in paths:
        if path.exists():
            shutil.rmtree(path)


def execute(config_path: Path) -> None:
    config = Config.load(config_path)
    bugs = read_bugs(Path(config.bugs_path))
    root = Path(config.output_root).parent
    for bug in bugs:
        result_file = Path(config.output_root) / bug / "result.json"
        if result_file.exists():
            result = json.loads(result_file.read_text())
            if result.get("evaluated") and result.get("pipeline_complete"):
                cleanup(config, bug)
                continue
        log = root / "worker-logs" / f"{bug}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        artifacts = Path(config.output_root) / bug
        checkout = Path(config.work_root).resolve() / bug
        if checkout.exists() and not (artifacts / "metadata.json").exists():
            shutil.rmtree(checkout)
        # Every worker checks the same frozen selection and run configuration.
        with log.open("a") as handle:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "debuglab.study",
                    "one",
                    "--config",
                    str(config_path),
                    "--bug",
                    bug,
                ],
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            started = time.monotonic()
            timed_out = False
            try:
                process.wait(timeout=config.bug_timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        timed_out = timed_out or (artifacts / "budget-timeout.json").exists()
        save(
            root / "worker-status" / f"{bug}.json",
            {
                "bug": bug,
                "seconds": time.monotonic() - started,
                "returncode": process.returncode,
                "timed_out": timed_out,
            },
        )
        if process.returncode:
            print(f"[{bug}] needs resume; see {log}", flush=True)
        else:
            print(f"[{bug}] evaluated and cleaned", flush=True)
        cleanup(config, bug)


def one(config_path: Path, bug: str) -> None:
    from debuglab.cli import fingerprint, sealed
    from debuglab.model import TokenBudget
    from debuglab.pipeline import run_bug

    config = Config.load(config_path)
    if bug not in read_bugs(Path(config.bugs_path)):
        raise ValueError("Bug does not belong to the selected evaluation set")
    run_freeze = Path(config.output_root).parent / "run-fingerprint.json"
    if run_freeze.exists() and json.loads(run_freeze.read_text()) != fingerprint(config_path):
        raise RuntimeError("Run code/config/selection changed before worker start")
    deadline = time.monotonic() + config.bug_timeout - 5
    deadline_key = "DEBUGLAB_BUG_DEADLINE_MONOTONIC"
    previous_deadline = os.environ.get(deadline_key)
    os.environ[deadline_key] = str(deadline)

    def expire(signum: int, frame: object) -> None:
        for path in (Path(config.output_root) / bug / "calls").glob("*.json"):
            record = json.loads(path.read_text())
            if record.get("status") == "IN_FLIGHT":
                record.update(status="TIMEOUT", error="Overall bug budget expired; never resampled")
                save(path, record)
        save(
            Path(config.output_root) / bug / "budget-timeout.json",
            {
                "bug": bug,
                "deadline": deadline,
                "deadline_clock": "monotonic",
                "unattempted_conditions_not_imputed": True,
            },
        )
        raise BugDeadline(f"Overall bug budget expired: {bug}")

    previous_handler = signal.signal(signal.SIGALRM, expire)
    signal.alarm(config.bug_timeout - 5)
    try:
        output = run_bug(config, bug, TokenBudget(config.model))
        sealed(config_path, output)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_deadline is None:
            os.environ.pop(deadline_key, None)
        else:
            os.environ[deadline_key] = previous_deadline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["execute", "one"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bug")
    args = parser.parse_args()
    if args.command == "execute":
        execute(args.config)
    elif args.bug:
        one(args.config, args.bug)
    else:
        parser.error("one requires --bug")


if __name__ == "__main__":
    main()
