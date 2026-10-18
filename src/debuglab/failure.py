"""Failure information and test source, frozen before model conditions diverge."""

from __future__ import annotations

import re
from pathlib import Path

from debuglab.harness import Harness, failing_count
from debuglab.io import digest, save
from debuglab.source import PARSER, Method, parse_file, walk


def compact(failure: dict, project_classes: frozenset[str]) -> tuple[dict, list[dict]]:
    """Deduplicate diagnostics while retaining associations with every triggering test."""
    messages, frames, observations, omitted = [], [], [], []
    message_ids: dict[str, int] = {}
    frame_ids: dict[str, int] = {}
    classes = {name.replace("$", ".") for name in project_classes}
    classes.update(test.split("::")[0].replace("$", ".") for test in failure["triggers"])
    for error in failure["errors"]:
        mids, fids = [], []
        for number, original in enumerate(error["error"].splitlines(), 1):
            line = original.strip()
            reason = None
            frame = re.match(r"at\s+(?:[\w.@-]+/)?([\w.$]+)\.[\w$<>]+\([^)]*\)", line)
            if not line or line.startswith("--- "):
                reason = "test_header_already_recorded_or_blank"
            elif frame and not any(
                frame[1].replace("$", ".") == name
                or frame[1].replace("$", ".").startswith(name + ".")
                for name in classes
            ):
                reason = "non_project_stack_frame"
            else:
                values, ids, selected = (
                    (frames, frame_ids, fids) if frame else (messages, message_ids, mids)
                )
                if line in ids:
                    reason = "repeated_diagnostic_replaced_by_reference"
                else:
                    ids[line] = len(values)
                    values.append(line)
                if ids[line] not in selected:
                    selected.append(ids[line])
            if reason:
                omitted.append({"test": error["test"], "original_line": number, "reason": reason})
        observations.append({"test": error["test"], "message_ids": mids, "project_frame_ids": fids})
    return {
        "exception_and_assertion_messages": messages,
        "project_frames": frames,
        "failures": observations,
    }, omitted


def test_hierarchy(
    file: Path, class_name: str, root: Path, source_root: Path
) -> list[tuple[Path, list[Method], str]]:
    """Resolve the trigger's original test-source superclass chain, without runtime hints."""
    units, seen = [], set()
    while file.exists():
        if class_name in seen:
            raise RuntimeError(f"Cyclic test superclass chain: {class_name}")
        seen.add(class_name)
        methods, scaffold = parse_file(file, root)
        units.append((file, [m for m in methods if m.class_name == class_name], scaffold))
        data = file.read_bytes()
        tree = PARSER.parse(data)
        target = None
        for node in walk(tree.root_node):
            if node.type != "class_declaration":
                continue
            owners, current = [], node
            while current:
                if current.type == "class_declaration":
                    name = current.child_by_field_name("name")
                    if name:
                        owners.append(data[name.start_byte : name.end_byte].decode())
                current = current.parent
            if "$".join(reversed(owners)) == class_name.rsplit(".", 1)[-1]:
                target = node
                break
        superclass = target.child_by_field_name("superclass") if target else None
        if not superclass:
            break
        parent = re.sub(r"^extends\s+", "", superclass.text.decode()).split("<", 1)[0].strip()
        package = class_name.rsplit(".", 1)[0] if "." in class_name else ""
        imports = re.findall(r"\bimport\s+(?!static\b)([\w.*]+)\s*;", data.decode())
        explicit = [name for name in imports if name.rsplit(".", 1)[-1] == parent]
        names = (
            [parent]
            if "." in parent
            else explicit
            or [
                f"{package}.{parent}" if package else parent,
                *(name[:-1] + parent for name in imports if name.endswith(".*")),
            ]
        )
        candidates = [(name, source_root / (name.replace(".", "/") + ".java")) for name in names]
        existing = [(name, path) for name, path in candidates if path.exists()]
        if not existing:
            break  # External framework superclass has no project source to supply.
        if len(existing) != 1:
            raise RuntimeError(f"Ambiguous test superclass: {class_name} extends {parent}")
        class_name, file = existing[0]
    return units


def gather(harness: Harness, meta: dict, output: Path) -> dict:
    triggers = meta["tests.trigger"].splitlines()
    errors, seeds, frames = [], set(), []
    test_units: dict[str, dict] = {}
    for trigger in triggers:
        result = harness.test(trigger)
        if failing_count(result) < 1:
            raise RuntimeError(f"Non-reproducing trigger {trigger}")
        failure_file = harness.root / "failing_tests"
        error = failure_file.read_text() if failure_file.exists() else result.output
        errors.append({"test": trigger, "error": error})
        class_name, method_name = trigger.split("::")
        test_file = (
            harness.root
            / meta["dir.src.tests"]
            / (class_name.split("$")[0].replace(".", "/") + ".java")
        )
        if not test_file.exists():
            raise RuntimeError(f"Trigger source not found: {test_file}")
        hierarchy = test_hierarchy(
            test_file, class_name, harness.root, harness.root / meta["dir.src.tests"]
        )
        defining = next(
            (
                index
                for index, (_, methods, _) in enumerate(hierarchy)
                if any(m.name == method_name for m in methods)
            ),
            None,
        )
        if defining is None:
            raise RuntimeError(f"Trigger method not found: {trigger}")
        fixture_names: set[str] = set()
        for depth, (file, methods, skeleton) in enumerate(hierarchy):
            selected = [m for m in methods if depth == defining and m.name == method_name]
            fixtures = [
                m
                for m in methods
                if m.name in {"setUp", "tearDown", "before", "after"}
                and m.name not in fixture_names
            ]
            fixture_names.update(m.name for m in fixtures)
            scaffold = "\n".join(
                line for line in skeleton.splitlines() if "// body omitted" not in line
            )
            relative = file.relative_to(harness.root).as_posix()
            unit = test_units.setdefault(relative, {"scaffold": scaffold, "methods": {}})
            for method in selected + fixtures:
                unit["methods"][method.id] = method
                seeds.update(method.calls)
        for match in re.finditer(r"\bat\s+([\w.$]+)\.([\w$<>]+)\(([^:()]+):(\d+)\)", error):
            frames.append(
                {"class": match[1], "method": match[2], "file": match[3], "line": int(match[4])}
            )
    sources = []
    for file, unit in sorted(test_units.items()):
        bodies = sorted(unit["methods"].values(), key=lambda method: method.start)
        sources.append(
            f"TEST {file}\n{unit['scaffold']}\n"
            + "\n\n".join(method.numbered() for method in bodies)
        )
    data = {
        "triggers": triggers,
        "errors": errors,
        "test_source": sources,
        "called_names": sorted(seeds),
        "frames": frames,
    }
    save(output / "failure.json", data)
    data["hash"] = digest((output / "failure.json").read_bytes())
    save(output / "failure-hash.json", {"hash": data["hash"]})
    return data
