"""Strict original-line edit blocks and isolated patch evaluation."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from debuglab.harness import Harness, failing_count, sanitize
from debuglab.source import PARSER


class Status(StrEnum):
    INVALID_FORMAT = "INVALID_FORMAT"
    INVALID_APPLY = "INVALID_APPLY"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    COMPILE_ERROR = "COMPILE_ERROR"
    FAILS_TRIGGER = "FAILS_TRIGGER"
    FAILS_SUITE = "FAILS_SUITE"
    PLAUSIBLE = "PLAUSIBLE"
    MODEL_ERROR = "MODEL_ERROR"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class Edit:
    file: str
    start_line: int
    end_line: int
    replacement: str


def parse(raw: str) -> list[Edit]:
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"edits"}:
        raise ValueError("Expected exactly one JSON key: edits")
    edits = data["edits"]
    if not isinstance(edits, list) or not 1 <= len(edits) <= 3:
        raise ValueError("Expected 1 to 3 edit blocks")
    result = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {
            "file",
            "start_line",
            "end_line",
            "replacement",
        }:
            raise ValueError("Incorrect edit fields")
        if (
            not isinstance(edit["file"], str)
            or not isinstance(edit["replacement"], str)
            or type(edit["start_line"]) is not int
            or type(edit["end_line"]) is not int
        ):
            raise ValueError("Incorrect edit field types")
        if "\x00" in edit["replacement"]:
            raise ValueError("NUL bytes are forbidden")
        result.append(Edit(**edit))
    return result


def apply(root: Path, edits: list[Edit], production: str, tests: str) -> list[Path]:
    grouped: dict[Path, list[Edit]] = {}
    production_root = (root / production).resolve()
    test_root = (root / tests).resolve()
    for edit in edits:
        relative = Path(edit.file)
        target = root / relative
        resolved = target.resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or target.is_symlink()
            or not resolved.is_relative_to(production_root)
            or resolved.is_relative_to(test_root)
            or target.suffix != ".java"
            or not target.is_file()
        ):
            raise ValueError(f"Forbidden target {edit.file}")
        if any(parent.is_symlink() for parent in target.parents if parent != root.parent):
            raise ValueError("Symlinks are forbidden")
        grouped.setdefault(target, []).append(edit)
    replacements = {}
    for path, blocks in grouped.items():
        lines = path.read_text().splitlines(keepends=True)
        ordered = sorted(blocks, key=lambda block: (block.start_line, block.end_line))
        last = 0
        for block in ordered:
            if (
                block.start_line < 1
                or block.end_line < block.start_line - 1
                or block.end_line > len(lines)
                or block.start_line > len(lines) + 1
                or block.start_line <= last
            ):
                raise ValueError("Invalid or overlapping original-line ranges")
            last = max(block.end_line, block.start_line)
        for block in reversed(ordered):
            replacement = block.replacement
            if replacement and not replacement.endswith("\n"):
                replacement += "\n"
            lines[block.start_line - 1 : block.end_line] = replacement.splitlines(keepends=True)
        replacements[path] = "".join(lines)
    # Validate every range before writing any file.
    for path, text in replacements.items():
        path.write_text(text)
    return list(replacements)


def evaluate(
    raw: str,
    original: Harness,
    meta: dict,
    destination: Path,
    *,
    log_root: Path | None = None,
) -> dict:
    result: dict = {"status": None, "edits": [], "detail": ""}
    try:
        edits = parse(raw)
    except (ValueError, TypeError) as error:
        return dict(result, status=Status.INVALID_FORMAT, detail=str(error))
    result["edits"] = [asdict(edit) for edit in edits]
    requested = destination
    suffix = 0
    # A saved response may outlive an interrupted evaluation. Preserve that checkout
    # and restart native evaluation from the original, without another model sample.
    while destination.exists():
        suffix += 1
        destination = requested.with_name(f"{requested.name}-resume-{suffix}")
    result["evaluation_checkout"] = str(destination)
    shutil.copytree(original.root, destination)
    sanitize(destination)
    try:
        changed = apply(destination, edits, meta["dir.src.classes"], meta["dir.src.tests"])
    except (ValueError, OSError, UnicodeError) as error:
        return dict(result, status=Status.INVALID_APPLY, detail=str(error))
    for path in changed:
        if PARSER.parse(path.read_bytes()).root_node.has_error:
            return dict(result, status=Status.SYNTAX_ERROR, detail=str(path))
    candidate = Harness(
        original.config,
        original.bug,
        (log_root or destination.parent) / (destination.name + "-logs"),
    )
    candidate.root = destination.resolve()
    compiled = candidate.compile()
    if compiled.timed_out:
        return dict(result, status=Status.TIMEOUT, detail="compile")
    if compiled.returncode:
        return dict(result, status=Status.COMPILE_ERROR, detail=compiled.output[-4000:])
    for trigger in meta["tests.trigger"].splitlines():
        tested = candidate.test(trigger)
        if tested.timed_out:
            return dict(result, status=Status.TIMEOUT, detail="trigger")
        try:
            count = failing_count(tested)
        except RuntimeError as error:
            return dict(result, status=Status.FAILS_TRIGGER, detail=str(error))
        if count:
            return dict(result, status=Status.FAILS_TRIGGER)
    suite = candidate.test()
    if suite.timed_out:
        return dict(result, status=Status.TIMEOUT, detail="suite")
    try:
        count = failing_count(suite)
    except RuntimeError as error:
        return dict(result, status=Status.FAILS_SUITE, detail=str(error))
    return dict(result, status=Status.FAILS_SUITE if count else Status.PLAUSIBLE)
