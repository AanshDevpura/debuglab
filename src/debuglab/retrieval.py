"""Score-blind retrieval from failures, executed sets, lexical and call proximity."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path

from rank_bm25 import BM25Okapi

from debuglab.config import Config
from debuglab.failure import compact
from debuglab.interface import MethodInterface
from debuglab.io import digest, save
from debuglab.model import TokenBudget, messages
from debuglab.source import Method, context_lines


def terms(text: str) -> list[str]:
    split = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.findall(r"[A-Za-z][A-Za-z0-9_]+", split.lower())


def balanced_order(ordered: list[str], by_id: dict[str, Method], reasons: dict) -> list[str]:
    """Round-robin relevant files within evidence tiers, preserving each file's order."""
    output = []
    tiers: dict[int, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
    for mid in ordered:
        row = reasons[mid]
        tier = (
            0
            if row["stack_frame"]
            else 1
            if row["executed"] and row["test_call"]
            else 2
            if row["executed"] or row.get("class_relevance")
            else 3
            if row["test_call"]
            else 4
        )
        tiers[tier][by_id[mid].file].append(mid)
    for tier in sorted(tiers):
        groups = list(tiers[tier].values())
        while groups:
            for group in groups:
                output.append(group.popleft())
            groups = [group for group in groups if group]
    return output


def build(
    methods: list[Method],
    skeletons: dict[str, str],
    failure: dict,
    executed_methods: frozenset[str],
    config: Config,
    budget: TokenBudget,
    output: Path,
    source_units: list[Method] | None = None,
) -> dict:
    if not isinstance(executed_methods, frozenset):
        raise TypeError("Coverage input must be an unranked frozen set")
    repair_units = source_units or []
    unit_ids = {m.id for m in repair_units}
    methods = methods + repair_units
    query = "\n".join(failure["test_source"]) + "\n" + json.dumps(failure["errors"])
    lexical = BM25Okapi([terms(m.signature + " " + m.text) or ["empty"] for m in methods])
    scores = lexical.get_scores(terms(query))
    maximum = max(scores, default=1) or 1
    by_name: dict[str, list[str]] = defaultdict(list)
    by_id = {m.id: m for m in methods}
    for method in methods:
        by_name[method.name].append(method.id)
    stack = {
        m.id
        for m in methods
        for f in failure["frames"]
        if m.class_name == f["class"] and m.start <= f["line"] <= m.end
    }
    direct = {mid for name in failure["called_names"] for mid in by_name[name]}
    # Class units inherit source relevance, not coverage scores or invented method execution.
    relevant_classes = {by_id[mid].class_name for mid in stack | direct | set(executed_methods)}
    seeds = stack | direct | set(executed_methods)
    adjacency: dict[str, set[str]] = defaultdict(set)
    # Conservative static call graph: unresolved overloads retain all name matches.
    for method in methods:
        for called in method.calls:
            for target in by_name[called]:
                adjacency[method.id].add(target)
                adjacency[target].add(method.id)
    distance = {mid: 0 for mid in seeds}
    queue = deque(sorted(seeds))
    while queue:
        current = queue.popleft()
        if distance[current] >= config.call_depth:
            continue
        for target in sorted(adjacency[current]):
            if target not in distance:
                distance[target] = distance[current] + 1
                queue.append(target)
    priority = {}
    reasons = {}
    for i, method in enumerate(methods):
        proximity = distance.get(method.id)
        reasons[method.id] = {
            "stack_frame": method.id in stack,
            "test_call": method.id in direct,
            "executed": method.id in executed_methods,
            "bm25": float(scores[i]),
            "call_distance": proximity,
            "class_unit": method.id in unit_ids,
            "class_relevance": method.id in unit_ids and method.class_name in relevant_classes,
        }
        priority[method.id] = (
            100 * (method.id in stack)
            + 30 * (method.id in direct)
            + 50 * (method.id in executed_methods)
            + 8 * float(scores[i]) / maximum
            + (2 / (proximity + 1) if proximity is not None else 0)
            + 50 * (method.id in unit_ids and method.class_name in relevant_classes)
        )
    ordered = sorted(by_id, key=lambda mid: (-priority[mid], mid))
    candidates = set(ordered[: config.retrieval_candidates]) | seeds
    if config.balanced_retrieval:
        ordered = balanced_order(ordered, by_id, reasons)
        candidates = set(ordered[: config.retrieval_candidates]) | seeds
    # Reserve hints using method ID lengths only; never inspect any spectrum score.
    longest = sorted(methods, key=lambda m: len(m.id), reverse=True)[: config.hint_candidates]
    worst_hint = {
        "methods": [
            {"method_id": m.id, "lines": [99999] * 10, "score": 0.123456789, "rank": 9999.5}
            for m in longest
        ]
    }
    prefix = (
        "FROZEN DEBUGGING CONTEXT\n"
        "All method-body lines use original file line numbers. "
        "Class skeletons are abbreviated declarations, not replacement text.\n\n"
        + "\n\n".join(failure["test_source"])
        + "\n\nOBSERVED FAILURES:\n"
        + json.dumps(failure["errors"], indent=2)
    )

    def fits(text: str) -> bool:
        if config.method_interface:
            interface = MethodInterface(
                text,
                methods,
                config.hint_candidates,
                config.line_array_edits,
                config.java_source_guidance,
                repair_units,
                config.executable_line_hints,
            )
            # Bound all possible hints using only source identities, never coverage scores.
            units = sorted(interface.units.values(), key=lambda m: len(m.id), reverse=True)
            reserved = {
                "methods": [
                    {"id": m.id, "score": 0.12345678901234567, "rank": 999999.5}
                    for m in units[: config.hint_candidates]
                ],
                "statements": [
                    {"id": location, "score": 0.12345678901234567, "rank": 999999.5}
                    for location in sorted(
                        {
                            f"{m.file}:{n}"
                            for m in interface.repair_units.values()
                            for n in range(m.start, m.end + 1)
                        },
                        key=lambda location: (-len(location), location),
                    )[: config.hint_candidates]
                ],
            }
            return (
                max(
                    budget.messages_count(interface.prompt(task, reserved))
                    for task in ("localize", "repair")
                )
                <= budget.input_limit
            )
        return (
            max(
                budget.messages_count(messages(text, task, worst_hint))
                for task in ("localize", "repair")
            )
            <= budget.input_limit
        )

    failure_omissions = []
    if not fits(prefix):
        diagnostics, failure_omissions = compact(
            failure, frozenset(method.class_name for method in methods)
        )
        prefix = prefix[: prefix.index("\n\nOBSERVED FAILURES:\n")] + (
            "\n\nOBSERVED FAILURES (deduplicated; IDs reference the lists below):\n"
            + json.dumps(diagnostics, indent=2)
        )
        if not fits(prefix):
            raise RuntimeError("Failure info alone exceeds budget after deduplication")
    context = prefix
    included, files, omitted = [], set(), []
    unit_lines: dict[str, set[int]] = defaultdict(set)
    for unit in repair_units:
        unit_lines[unit.file].update(range(unit.start, unit.end + 1))
    for mid in ordered:
        method = by_id[mid]
        if mid not in candidates:
            omitted.append({"method_id": mid, "reason": "outside_retrieved_set", **reasons[mid]})
            continue
        addition = ""
        if method.file not in files:
            scaffold = "\n".join(
                line
                for line in skeletons[method.file].splitlines()
                if "// body omitted" not in line
                and not (
                    (number := re.match(r"^(\d+):", line))
                    and int(number[1]) in unit_lines[method.file]
                )
            )
            addition += f"\n\nCLASS SKELETON {method.file}\n{scaffold}"
        section = "SOURCE UNIT" if mid in unit_ids else "METHOD"
        addition += f"\n\n{section} {method.id}\n{method.numbered()}"
        if not fits(context + addition):
            omitted.append({"method_id": mid, "reason": "dropped_for_budget", **reasons[mid]})
            continue
        context += addition
        files.add(method.file)
        included.append(mid)
    encoded = context.encode("utf-8")
    context_path = output / "context.txt"
    if context_path.exists() and context_path.read_bytes() != encoded:
        raise RuntimeError("Frozen context cannot be changed in place")
    context_path.write_bytes(encoded)
    manifest = {
        "hash": digest(encoded),
        "failure_hash": failure["hash"],
        "method_ids": [mid for mid in included if mid not in unit_ids],
        "source_unit_ids": [mid for mid in included if mid in unit_ids],
        "files": sorted(files),
        "lines": context_lines(context),
        "tokens": budget.count(context),
        "input_limit": budget.input_limit,
        "omitted": omitted,
        "failure_omissions": failure_omissions,
        "skeleton_omissions": [
            {
                "method_id": m.id,
                "reason": "declaration_in_included_body"
                if m.id in included
                else "budget_reserved_for_method_bodies",
            }
            for m in methods
            if m.file in files
        ],
        "executed_methods": sorted(executed_methods),
        "retrieval_policy": (
            "evidence-tier-file-balanced-v3"
            if config.balanced_retrieval
            else "executed-priority-compact-skeleton-bm25-name-callgraph-v2"
        ),
    }
    save(output / "context-manifest.json", manifest)
    return manifest
