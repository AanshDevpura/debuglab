"""Run the pinned 100-bug GPT evaluation with the original $60 transport guard."""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from debuglab.cohort_run import prepare_run
from debuglab.config import Config
from debuglab.io import save


def descendants(pid: int) -> set[int]:
    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True)
    links = [tuple(map(int, row.split())) for row in rows.splitlines()]
    found = {pid}
    while True:
        more = {child for child, parent in links if parent in found} - found
        if not more:
            return found
        found |= more


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/study-gpt54-mini.json"))
    parser.add_argument(
        "--output", type=Path, help="New output root, for example outputs/gpt-run-1"
    )
    args = parser.parse_args()
    cfg = Config.load(args.config)
    if cfg.model.model != "gpt-5.4-mini-2026-03-17":
        parser.error("This guard only supports the pinned study model and pricing")
    if not os.environ.get(cfg.model.api_key_env):
        parser.error(f"Set {cfg.model.api_key_env} in your environment first")
    root = args.output or Path(cfg.output_root).parent
    root.parent.mkdir(parents=True, exist_ok=True)
    with (root.parent / (root.name + "-supervisor.lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / "TRANSPORT_STOP.json").exists():
            parser.error(
                "Transport stopped. Preserve evidence; diagnose before using a new boundary"
            )
        # Freeze the new run before adding ledger files; prepare_run rejects
        # pre-existing, unowned artifacts. Repeated commands reuse the freeze.
        prepare_run(args.config, output=root)
        ledger = root / "spending-ledger.json"
        if not ledger.exists():
            if list(root.glob("runs/*/calls/*.json")):
                parser.error("Existing calls without a spending ledger; refusing a new ledger")
            save(
                ledger,
                {
                    "limit_micro_usd": 60_000_000,
                    "reserved_micro_usd": 0,
                    "reserved_requests": 0,
                    "request_hashes": [],
                },
            )
        env = dict(os.environ)
        runtime = Path(__file__).resolve().parent / "hosted_runtime"
        env["PYTHONPATH"] = str(runtime) + os.pathsep + env.get("PYTHONPATH", "")
        env["DEBUGLAB_HOSTED_SPENDING_ROOT"] = str(root.resolve())
        subprocess.run(
            [sys.executable, "-c", "import httpx; assert httpx.post.__module__ == 'hosted_guard'"],
            env=env,
            check=True,
        )
        command = [
            sys.executable,
            "-u",
            "-m",
            "debuglab.cohort_run",
            "--config",
            str(args.config),
            "--output",
            str(root),
        ]
        child = subprocess.Popen(command, env=env, start_new_session=True)
        try:
            while child.poll() is None:
                if (root / "TRANSPORT_STOP.json").exists():
                    time.sleep(2)  # Let the failed API response finish saving.
                    affected = descendants(child.pid)
                    save(
                        root / "SUPERVISOR_STOP.json",
                        {"affected_pids": sorted(affected), "no_automatic_retry": True},
                    )
                    for pid in sorted(affected, reverse=True):
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        for pid in affected:
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    raise SystemExit("API/budget failure: inspect TRANSPORT_STOP.json and receipts")
                time.sleep(2)
            raise SystemExit(child.returncode)
        except KeyboardInterrupt:
            for pid in descendants(child.pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            raise


if __name__ == "__main__":
    main()
