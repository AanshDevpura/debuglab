"""Sealed, offline interface certificates and explicitly oracle-selected buggy contexts.

No inference and no changes to benchmark results. A failed certificate means the
reference transformation was not certified, not that every possible repair fails.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from debuglab.config import Config
from debuglab.interface import MethodInterface
from debuglab.io import digest, save
from debuglab.method_contract import validate_replacement
from debuglab.model import TokenBudget
from debuglab.source import Method


def reverse_changes(patch: str) -> list[dict]:
    """Read Defects4J fixed->buggy patches as exact buggy->fixed line blocks."""
    lines = patch.splitlines()
    if any(line.startswith("\\ No newline") for line in lines):
        raise ValueError("No-newline patch requires byte-level certification")
    changes = []
    file = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("+++ "):
            file = line[4:].split("\t")[0]
            if file.startswith("b/"):
                file = file[2:]
            if file == "/dev/null" or Path(file).is_absolute() or ".." in Path(file).parts:
                raise ValueError("File creation/deletion or unsafe path requires external edits")
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if not match:
            i += 1
            continue
        if file is None:
            raise ValueError("Hunk without file")
        fixed_left, buggy_left = int(match[2] or 1), int(match[4] or 1)
        cursor = int(match[3])
        # Zero-length new-side hunk coordinates describe insertion AFTER this line.
        if buggy_left == 0:
            cursor += 1
        i += 1
        block = None
        while i < len(lines) and (fixed_left or buggy_left):
            row = lines[i]
            if row.startswith("\\ No newline"):
                raise ValueError("No-newline patch requires byte-level certification")
            if not row or row[0] not in " +-":
                raise ValueError("Malformed or incomplete patch hunk")
            mark, text = row[0], row[1:]
            if mark == " ":
                if block:
                    changes.append(block)
                    block = None
                fixed_left -= 1
                buggy_left -= 1
                cursor += 1
            else:
                if block is None:
                    block = {"file": file, "start": cursor, "buggy": [], "fixed": []}
                if mark == "+":
                    block["buggy"].append(text)
                    buggy_left -= 1
                    cursor += 1
                else:
                    block["fixed"].append(text)
                    fixed_left -= 1
            if fixed_left < 0 or buggy_left < 0:
                raise ValueError("Patch hunk length mismatch")
            i += 1
        if block:
            changes.append(block)
    if not changes:
        raise ValueError("No source changes")
    return changes


def reference_replacements(methods: list[Method], changes: list[dict]) -> dict[str, str]:
    """Certify every reference change lies inside a replaceable original method."""
    assigned = {}
    for change in changes:
        start = change["start"]
        stop = start + len(change["buggy"]) - 1
        candidates = [
            m
            for m in methods
            if m.file == change["file"]
            and (m.start <= start <= stop <= m.end if change["buggy"] else m.start < start <= m.end)
        ]
        if not candidates:
            raise ValueError("Reference change outside a complete original method")
        method = min(candidates, key=lambda m: m.end - m.start)
        assigned.setdefault(method.id, (method, []))[1].append(change)
    selected = [entry[0] for entry in assigned.values()]
    for i, left in enumerate(selected):
        for right in selected[i + 1 :]:
            if left.file == right.file and left.start <= right.end and right.start <= left.end:
                raise ValueError(
                    "Overlapping original method ranges require separate certification"
                )
    result = {}
    for mid, (method, edits) in assigned.items():
        lines = method.text.splitlines()
        for change in sorted(edits, key=lambda c: c["start"], reverse=True):
            offset = change["start"] - method.start
            old = change["buggy"]
            if lines[offset : offset + len(old)] != old:
                raise ValueError("Patch buggy text differs from saved original method")
            lines[offset : offset + len(old)] = change["fixed"]
        replacement = "\n".join(lines)
        validate_replacement(method.text, replacement)
        result[mid] = replacement
    return result


def oracle_context(folder: Path, methods: list[Method], required: list[str]) -> str:
    """Buggy text only. Gold chooses identities, never supplies fixed code to a prompt."""
    old = (folder / "context.txt").read_text()
    prefix = re.split(r"\n\nCLASS SKELETON |\n\nMETHOD ", old, maxsplit=1)[0]
    skeletons = json.loads((folder / "skeletons.json").read_text())
    by_id = {m.id: m for m in methods}
    result = (
        "ORACLE DIAGNOSTIC: method identities selected using known reference fault locations. "
        "All numbered source below is the original BUGGY version.\n" + prefix
    )
    files = set()
    for mid in required:
        method = by_id[mid]
        if method.file not in files:
            scaffold = "\n".join(
                line
                for line in skeletons[method.file].splitlines()
                if "// body omitted" not in line
            )
            result += f"\n\nCLASS SKELETON {method.file}\n{scaffold}"
            files.add(method.file)
        result += f"\n\nMETHOD {mid}\n{method.numbered()}"
    return result


def prompt_counts(interface: MethodInterface, config: Config, budget: TokenBudget) -> dict:
    # Bound every condition, including the longest permissible source-only hints.
    hint = {
        "methods": [
            {"id": m.id, "score": 0.12345678901234567, "rank": 999999.5}
            for m in list(interface.units.values())[: config.hint_candidates]
        ],
        "statements": [
            {"id": location, "score": 0.12345678901234567, "rank": 999999.5}
            for location in sorted(
                {
                    f"{m.file}:{n}"
                    for m in interface.units.values()
                    for n in range(m.start, m.end + 1)
                },
                key=lambda x: (-len(x), x),
            )[: config.hint_candidates]
        ],
    }
    counts = {
        task: budget.messages_count(interface.prompt(task, hint)) for task in ["localize", "repair"]
    }
    if not budget.exact:
        for task in counts:
            counts[task] += budget.count(json.dumps(interface.schema(task))) + 512
    return counts


def audit_bug(folder: Path, patch: Path, config: Config, budget: TokenBudget, output: Path) -> dict:
    methods = [Method(**m) for m in json.loads((folder / "methods.json").read_text())]
    row = {
        "bug": folder.name,
        "reference_certified": False,
        "oracle_ready": False,
        "main_context_complete": False,
        "main_ready": False,
    }
    try:
        replacements = reference_replacements(methods, reverse_changes(patch.read_text()))
        required = sorted(replacements)
        manifest = json.loads((folder / "context-manifest.json").read_text())
        row.update(
            reference_methods=len(required),
            required_method_ids=required,
            main_context_complete=set(required) <= set(manifest["method_ids"]),
            patch_sha256=digest(patch.read_bytes()),
        )
        if not 1 <= len(required) <= 3:
            raise ValueError("Reference transformation exceeds three method replacements")
        row["reference_certified"] = True
        context = oracle_context(folder, methods, required)
        interface = MethodInterface(
            context,
            methods,
            config.hint_candidates,
            java_source_guidance=True,
            independent_statements=config.independent_statements,
            strict_method_replacements=config.strict_method_replacements,
        )
        assert set(required) == {m.id for m in interface.units.values()}
        answer = json.dumps(
            {
                "edits": [
                    {"method_id": key, "replacement": replacements[m.id]}
                    for key, m in interface.units.items()
                ]
            }
        )
        interface.edits(answer)  # Strict real interface, never model inference.
        counts = prompt_counts(interface, config, budget)
        tokens = budget.count(answer)
        row.update(
            prompt_tokens=counts,
            reference_output_tokens=tokens,
            input_limit=budget.input_limit,
            output_limit=config.model.max_output_tokens,
        )
        if max(counts.values()) > budget.input_limit:
            raise ValueError("Required buggy methods and diagnostics exceed input budget")
        # TokenBudget counts content, so reserve output framing/stop-token headroom.
        if tokens + 32 > config.model.max_output_tokens:
            raise ValueError("Complete reference replacements exceed output budget")
        destination = output / "oracle-contexts" / folder.name
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "context.txt").write_text(context)
        save(
            destination / "manifest.json",
            {
                "scope": "oracle diagnostic; not main retrieval",
                "method_ids": required,
                "hash": digest(context.encode()),
                "buggy_source_only": True,
                "reference_output_in_prompt": False,
                "prompt_tokens": counts,
            },
        )
        if row["main_context_complete"]:
            main = MethodInterface(
                (folder / "context.txt").read_text(),
                methods,
                config.hint_candidates,
                java_source_guidance=True,
                independent_statements=config.independent_statements,
                strict_method_replacements=config.strict_method_replacements,
            )
            main_answer = json.dumps(
                {
                    "edits": [
                        {"method_id": key, "replacement": replacements[m.id]}
                        for key, m in main.units.items()
                        if m.id in replacements
                    ]
                }
            )
            main.edits(main_answer)
            main_counts = prompt_counts(main, config, budget)
            row["main_prompt_tokens"] = main_counts
            row["main_reference_output_tokens"] = budget.count(main_answer)
            row["main_ready"] = (
                max(main_counts.values()) <= budget.input_limit
                and budget.count(main_answer) + 32 <= config.model.max_output_tokens
            )
        row["oracle_ready"] = True
        row["reason"] = "Exact reference transformation fits strict interface and budgets"
    except (ValueError, KeyError) as error:
        row["reason"] = str(error)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--bugs", type=Path, required=True)
    parser.add_argument(
        "--patches",
        type=Path,
        required=True,
        help="Installed Defects4J framework/projects directory",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(
        args.output.resolve().is_relative_to(p.resolve()) for p in [args.runs, args.patches]
    ) or args.output.resolve() in {args.config.resolve(), args.bugs.resolve()}:
        parser.error("Use a new output directory outside protected inputs")
    if args.output.exists():
        parser.error("Output must be a fresh immutable boundary")
    config = Config.load(args.config)
    budget = TokenBudget(config.model)
    bugs = json.loads(args.bugs.read_text())
    if len(bugs) != len(set(bugs)):
        parser.error("Duplicate selected bugs")
    args.output.mkdir(parents=True)
    rows = []
    for bug in bugs:
        project, number = bug.split("-")
        rows.append(
            audit_bug(
                args.runs / bug,
                args.patches / project / "patches" / f"{number}.src.patch",
                config,
                budget,
                args.output,
            )
        )
    summary = {
        "selected": len(rows),
        "reference_certified": sum(r["reference_certified"] for r in rows),
        "oracle_ready": sum(r["oracle_ready"] for r in rows),
        "certified_and_main_context_complete": sum(
            r["oracle_ready"] and r["main_context_complete"] for r in rows
        ),
        "frozen_main_ready": sum(r["main_ready"] for r in rows),
    }
    report = {
        "scope": "Post-hoc reference-based eligibility/oracle diagnostic, not gold-free "
        "retrieval or proof that alternative repairs are impossible. No model calls. "
        "Reference representability is not model success or native correctness proof.",
        "summary": summary,
        "per_bug": rows,
    }
    save(args.output / "readiness.json", report)
    save(args.output / "oracle-bugs.json", [r["bug"] for r in rows if r["oracle_ready"]])
    save(
        args.output / "main-covered-bugs.json",
        [r["bug"] for r in rows if r["main_ready"]],
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
