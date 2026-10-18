"""Durable JSON artifacts and subprocess execution with complete logs."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    seconds: float
    output: str
    timed_out: bool


def run(
    command: list[str],
    cwd: Path,
    log: Path,
    timeout: int = 600,
    extra_env: dict[str, str] | None = None,
) -> CommandResult:
    env = dict(os.environ, TZ="America/Los_Angeles", LC_ALL="en_US.UTF-8")
    env.update(extra_env or {})
    if "DEBUGLAB_BUG_DEADLINE_MONOTONIC" in env:
        # Use the worker's elapsed clock, never an adjustable calendar clock.
        # Preserve fractional remaining budgets instead of granting another second.
        timeout = min(
            timeout,
            max(0.001, float(env["DEBUGLAB_BUG_DEADLINE_MONOTONIC"]) - time.monotonic()),
        )
    elif "DEBUGLAB_BUG_DEADLINE" in env:
        # Compatibility for external callers of the previous wall-clock API.
        # Current workers always publish the monotonic deadline above.
        timeout = min(timeout, max(0.001, float(env["DEBUGLAB_BUG_DEADLINE"]) - time.time()))
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
    except BaseException:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    result = CommandResult(
        command,
        process.returncode,
        time.monotonic() - started,
        output.decode("utf-8", errors="replace"),
        timed_out,
    )
    save(log, asdict(result))
    return result
