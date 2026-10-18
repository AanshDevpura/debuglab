"""Buggy-only Defects4J harness. Never reads fixed versions or patch metadata."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from debuglab.config import Config
from debuglab.io import CommandResult, run
from debuglab.selection import BUG_ID

FORBIDDEN_SUFFIXES = {".patch", ".diff"}
VCS_NAMES = {".git", ".svn", ".hg", "CVS"}


def sanitize(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.name in VCS_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()


class Harness:
    def __init__(self, config: Config, bug: str, logs: Path):
        if not BUG_ID.fullmatch(bug):
            raise ValueError("Expected a Defects4J bug ID, e.g. Lang-1 or Cli-1")
        self.config = config
        self.bug = bug
        self.logs = logs
        self.root = Path(config.work_root).resolve() / bug
        self.counter = max(
            (
                int(p.name.split("-")[0])
                for p in logs.glob("*.json")
                if p.name.split("-")[0].isdigit()
            ),
            default=0,
        )

    def command(self, *args: str, cwd: Path | None = None) -> CommandResult:
        self.counter += 1
        return run(
            [str(Path(self.config.defects4j).resolve()), *args],
            cwd or self.root,
            self.logs / f"{self.counter:05d}-{args[0]}.json",
            self.config.command_timeout,
        )

    def checkout(self) -> None:
        if self.root.exists():
            raise FileExistsError(f"Refusing to overwrite checkout {self.root}")
        self.root.parent.mkdir(parents=True, exist_ok=True)
        project, number = self.bug.split("-")
        result = self.command(
            "checkout",
            "-p",
            project,
            "-v",
            number + "b",
            "-w",
            str(self.root),
            cwd=self.root.parent,
        )
        if result.returncode or result.timed_out:
            raise RuntimeError(f"Checkout failed: {result.output[-4000:]}")
        sanitize(self.root)

    def export(self, prop: str) -> str:
        allowed = {
            "dir.src.classes",
            "dir.src.tests",
            "dir.bin.classes",
            "dir.bin.tests",
            "cp.test",
            "cp.compile",
            "tests.trigger",
            "tests.all",
            "classes.relevant",
        }
        if prop not in allowed:
            raise ValueError(f"Metadata is not permitted: {prop}")
        target = self.logs.resolve() / f"export-{prop}.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        result = self.command("export", "-p", prop, "-o", str(target))
        if result.returncode:
            raise RuntimeError(result.output)
        return target.read_text().strip()

    def compile(self) -> CommandResult:
        return self.command("compile")

    def test(self, trigger: str | None = None) -> CommandResult:
        args = ("test", "-t", trigger) if trigger else ("test",)
        result = self.command(*args)
        failure_file = self.root / "failing_tests"
        if failure_file.exists():
            self.logs.mkdir(parents=True, exist_ok=True)
            (self.logs / f"{self.counter:05d}-test-failures.txt").write_bytes(
                failure_file.read_bytes()
            )
        return result


def failing_count(result: CommandResult) -> int:
    matches = re.findall(r"Failing tests:\s*(\d+)", result.output)
    if result.returncode or not matches:
        raise RuntimeError(f"No trustworthy test result: {result.output[-2000:]}")
    return int(matches[-1])
