"""Sealed fix derivation and post-run measurement."""

from __future__ import annotations

import difflib
import json
import re
import shutil
from pathlib import Path

from debuglab.config import Config
from debuglab.harness import sanitize
from debuglab.io import digest, run, save
from debuglab.source import Method, enclosing


def derive(config: Config, bug: str, artifacts: Path) -> dict:
    root = Path("work/gold").resolve() / bug
    root.mkdir(parents=True, exist_ok=True)
    cache = root / "locations.json"
    if cache.exists():
        gold = json.loads(cache.read_text())
        checked = permit_preexisting(config, bug, artifacts, gold)
        save(cache, checked)
        return checked
    project, number = bug.split("-")
    fixed = root / "fixed"
    if fixed.exists():
        shutil.rmtree(fixed)
    result = run(
        [
            str(Path(config.defects4j).resolve()),
            "checkout",
            "-p",
            project,
            "-v",
            number + "f",
            "-w",
            str(fixed),
        ],
        root,
        root / "checkout.json",
        config.command_timeout,
    )
    if result.returncode or result.timed_out:
        raise RuntimeError(result.output)
    sanitize(fixed)
    meta = json.loads((artifacts / "metadata.json").read_text())
    methods = [Method(**m) for m in json.loads((artifacts / "methods.json").read_text())]
    buggy = Path(config.work_root).resolve() / bug
    faulty_files, faulty_methods, faulty_lines, added = set(), set(), set(), set()
    for file in sorted((buggy / meta["dir.src.classes"]).rglob("*.java")):
        relative = file.relative_to(buggy).as_posix()
        other = fixed / relative
        old = file.read_text().splitlines()
        new = other.read_text().splitlines() if other.exists() else []
        matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
        for tag, i, j, a, b in matcher.get_opcodes():
            if tag == "equal":
                continue
            faulty_files.add(relative)
            added.update(line for line in new[a:b] if line.strip())
            if tag in {"replace", "delete"}:
                for line in range(i + 1, j + 1):
                    faulty_lines.add(f"{relative}:{line}")
                    method = enclosing(methods, relative, line)
                    if method:
                        faulty_methods.add(method.id)
            if tag == "insert":
                method = enclosing(methods, relative, i + 1) or enclosing(methods, relative, i)
                if method:
                    faulty_methods.add(method.id)
    if not faulty_files:
        raise RuntimeError(f"Gold fix contains no production changes: {bug}")
    gold = {
        "files": sorted(faulty_files),
        "methods": sorted(faulty_methods),
        "lines": sorted(faulty_lines),
        "added_lines": sorted(added),
        "method_target_mapped": bool(faulty_methods),
    }
    gold = permit_preexisting(config, bug, artifacts, gold)
    save(cache, gold)
    return gold


def recall(gold: dict, manifest: dict) -> dict:
    files = set(manifest["files"])
    methods = set(manifest["method_ids"])
    lines = {f"{file}:{line}" for file, numbers in manifest["lines"].items() for line in numbers}
    gold_files, gold_methods, gold_lines = map(set, (gold["files"], gold["methods"], gold["lines"]))
    omissions = {row["method_id"]: row for row in manifest["omitted"]}
    missing = []
    for mid in gold_methods - methods:
        row = omissions.get(mid, {})
        missing.append(
            {
                "method_id": mid,
                "reason": row.get("reason", "outside_retrieved_set"),
                "not_executed_by_failing_test": mid not in manifest["executed_methods"],
            }
        )
    missing_lines = []
    for location in sorted(gold_lines - lines):
        mid = gold.get("line_to_method", {}).get(location)
        row = omissions.get(mid, {})
        missing_lines.append(
            {
                "id": location,
                "method_id": mid,
                "reason": row.get(
                    "reason", "outside_method_body" if mid is None else "outside_retrieved_set"
                ),
                "not_executed_by_failing_test": (
                    mid not in manifest["executed_methods"] if mid is not None else None
                ),
                "outside_method_body": mid is None,
            }
        )
    return {
        "file_any": bool(gold_files & files),
        "file_all": gold_files <= files,
        "method_any": bool(gold_methods & methods),
        "method_all": bool(gold_methods) and gold_methods <= methods,
        "line_any": bool(gold_lines & lines) if gold_lines else None,
        "line_all": gold_lines <= lines if gold_lines else None,
        "line_fraction": len(gold_lines & lines) / len(gold_lines) if gold_lines else None,
        "line_eligible": bool(gold_lines),
        "missing": missing,
        "missing_lines": missing_lines,
    }


def metric(ranking: list[dict], targets: list[str], eligible: bool = True) -> dict:
    matches = [row["rank"] for row in ranking if row["id"] in targets]
    rank = min(matches) if matches else None
    return {
        "eligible": eligible,
        "rank": rank,
        **{
            f"top{k}": bool(rank is not None and rank <= k) if eligible else None for k in (1, 3, 5)
        },
    }


def leak_violations(gold: dict, artifacts: Path) -> list[dict]:
    """Reject novel fix lines; user explicitly permits preexisting buggy-source lines."""
    violations = []
    for path in sorted((artifacts / "calls").glob("*.json")):
        call = json.loads(path.read_text())
        for message in call["parameters"]["messages"]:
            prompt = re.sub(r"(?m)^\d+: ", "", message["content"])
            for line in gold["added_lines"]:
                if line in prompt and line not in gold.get("permitted_preexisting_lines", []):
                    violations.append(
                        {"call": path.name, "added_line_sha256": digest(line.encode())}
                    )
    return violations


def evaluate(config: Config, bug: str, artifacts: Path) -> dict:
    result_path = artifacts / "result.json"
    result = json.loads(result_path.read_text())
    manifest = json.loads((artifacts / "context-manifest.json").read_text())
    if digest((artifacts / "context.txt").read_bytes()) != manifest["hash"]:
        raise RuntimeError("Frozen context changed")
    gold = derive(config, bug, artifacts)
    result["context_recall"] = recall(gold, manifest)
    result["gold_eligibility"] = {
        "line": bool(gold["lines"]),
        "method": True,
        "method_target_mapped": bool(gold["methods"]),
    }
    for ranking in result["localization"].values():
        ranking["metrics"] = {
            "line": metric(ranking["statement"], gold["lines"], bool(gold["lines"])),
            "method": metric(ranking["method"], gold["methods"]),
        }
    result["leak_violations"] = leak_violations(gold, artifacts)
    result["evaluated"] = True
    save(result_path, result)
    return result


def preexists(line: str, original_lines: set[str]) -> bool:
    """Match the same literal fragment searched by the leak guard in original source."""
    fragment = line.strip()
    return fragment in original_lines or any(fragment in old for old in original_lines)


def audit_context(config: Config, bug: str, artifacts: Path) -> dict:
    """Fail closed before sending original source if the literal leak rule conflicts."""
    from debuglab.model import messages

    gold = derive(config, bug, artifacts)
    text = (artifacts / "context.txt").read_bytes().decode("utf-8")
    prompt = "\n".join(message["content"] for message in messages(text, "localize", None))
    prompt = re.sub(r"(?m)^\d+: ", "", prompt)
    meta = json.loads((artifacts / "metadata.json").read_text())
    buggy_lines = {
        line
        for directory in (meta["dir.src.classes"], meta.get("dir.src.tests"))
        if directory is not None
        for path in (Path(config.work_root).resolve() / bug / directory).rglob("*.java")
        for line in path.read_text().splitlines()
    }
    original_normalized = {old.strip() for old in buggy_lines}
    if not buggy_lines:
        original_normalized = {line.strip() for line in gold.get("permitted_preexisting_lines", [])}
    collisions = [
        {
            "added_line_sha256": digest(line.encode()),
            "already_in_buggy_source": preexists(line, original_normalized),
            "structural_only": not bool(re.search(r"[A-Za-z0-9]", line)),
        }
        for line in gold["added_lines"]
        if line in prompt
    ]
    violations = [item for item in collisions if not item["already_in_buggy_source"]]
    measurement = recall(gold, json.loads((artifacts / "context-manifest.json").read_text()))
    record = {
        "bug": bug,
        "context_hash": digest(text.encode()),
        "research_prompt_sent": False,
        "novel_added_line_violations": violations,
        "permitted_preexisting_collisions": len(collisions) - len(violations),
        "context_recall": measurement,
        "passed": not violations,
    }
    save(artifacts / "pre-call-audit.json", record)
    return record


def permit_preexisting(config: Config, bug: str, artifacts: Path, gold: dict) -> dict:
    """User-authorized guard exception, computed inside sealed evaluation only."""
    meta = json.loads((artifacts / "metadata.json").read_text())
    source = Path(config.work_root).resolve() / bug / meta["dir.src.classes"]
    if not source.exists() and "permitted_preexisting_lines" in gold:
        # The sealed cache was derived before cleanup; do not widen its allowlist.
        return gold
    original_lines = {
        line
        for directory in (meta["dir.src.classes"], meta.get("dir.src.tests"))
        if directory is not None
        for path in (Path(config.work_root).resolve() / bug / directory).rglob("*.java")
        for line in path.read_text().splitlines()
    }
    normalized_original = {line.strip() for line in original_lines}
    gold["permitted_preexisting_lines"] = [
        line for line in gold["added_lines"] if preexists(line, normalized_original)
    ]
    methods = [Method(**m) for m in json.loads((artifacts / "methods.json").read_text())]
    gold["line_to_method"] = {}
    for location in gold["lines"]:
        file, line_number = location.rsplit(":", 1)
        method = enclosing(methods, file, int(line_number))
        gold["line_to_method"][location] = method.id if method else None
    return gold
