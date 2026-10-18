"""Java AST method units and skeletons, using only the buggy production tree."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import tree_sitter_java
from tree_sitter import Language, Node, Parser

PARSER = Parser(Language(tree_sitter_java.language()))
METHOD_TYPES = {"method_declaration", "constructor_declaration"}
CLASS_TYPES = {"class_declaration", "interface_declaration", "enum_declaration"}


def walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from walk(child)


@dataclass(frozen=True)
class Method:
    id: str
    file: str
    class_name: str
    name: str
    signature: str
    start: int
    end: int
    text: str
    calls: tuple[str, ...]

    def numbered(self) -> str:
        lines = self.text.splitlines()
        return "\n".join(f"{self.start + i}: {line}" for i, line in enumerate(lines))

    def record(self) -> dict:
        return asdict(self)


def parse_file(path: Path, root: Path) -> tuple[list[Method], str]:
    data = path.read_bytes()
    original_lines = data.decode().splitlines()
    tree = PARSER.parse(data)
    relative = path.relative_to(root).as_posix()
    package = re.search(rb"\bpackage\s+([\w.]+)\s*;", data)
    prefix = package.group(1).decode() + "." if package else ""
    methods = []
    skeleton = []
    for node in walk(tree.root_node):
        if node.type in CLASS_TYPES:
            body = node.child_by_field_name("body")
            stop = body.start_byte if body else node.end_byte
            skeleton.extend(
                f"{node.start_point.row + offset + 1}: {line}"
                for offset, line in enumerate(data[node.start_byte : stop].decode().splitlines())
            )
        elif node.type == "field_declaration":
            skeleton.extend(
                f"{row + 1}: {original_lines[row]}"
                for row in range(node.start_point.row, node.end_point.row + 1)
            )
        elif node.type in METHOD_TYPES:
            parent = node.parent
            owners = []
            while parent:
                if parent.type in CLASS_TYPES:
                    name = parent.child_by_field_name("name")
                    if name:
                        owners.append(data[name.start_byte : name.end_byte].decode())
                parent = parent.parent
            name_node = node.child_by_field_name("name")
            if not name_node or not owners:
                continue
            name = data[name_node.start_byte : name_node.end_byte].decode()
            class_name = prefix + "$".join(reversed(owners))
            body = node.child_by_field_name("body")
            stop = body.start_byte if body else node.end_byte
            signature = " ".join(data[node.start_byte : stop].decode().split())
            start, end = node.start_point.row + 1, node.end_point.row + 1
            # Include full original lines, not node slices, for safe line-range edits.
            text = "\n".join(original_lines[start - 1 : end])
            calls = []
            for child in walk(node):
                if child.type == "method_invocation":
                    called = child.child_by_field_name("name")
                    if called:
                        calls.append(data[called.start_byte : called.end_byte].decode())
            methods.append(
                Method(
                    f"{relative}::{class_name}#{name}@{start}",
                    relative,
                    class_name,
                    name,
                    signature,
                    start,
                    end,
                    text,
                    tuple(sorted(set(calls))),
                )
            )
            skeleton.append(f"{start}: {signature}; // body omitted")
    return methods, "\n".join(skeleton)


def index(root: Path, production: str) -> tuple[list[Method], dict[str, str]]:
    methods, skeletons = [], {}
    for path in sorted((root / production).rglob("*.java")):
        if path.is_symlink():
            raise ValueError("Source symlinks are forbidden")
        found, skeleton = parse_file(path, root)
        methods.extend(found)
        skeletons[path.relative_to(root).as_posix()] = skeleton
    return methods, skeletons


def enclosing(methods: list[Method], file: str, line: int) -> Method | None:
    matches = [m for m in methods if m.file == file and m.start <= line <= m.end]
    return min(matches, key=lambda m: m.end - m.start) if matches else None


def context_lines(context: str) -> dict[str, list[int]]:
    """Inventory numbered production lines actually rendered, including class fields."""
    sections = re.split(r"(?m)^(?:CLASS SKELETON|METHOD|SOURCE UNIT) (.+)\n", context)
    lines: dict[str, set[int]] = {}
    for offset in range(1, len(sections), 2):
        file = sections[offset].split("::", 1)[0]
        lines.setdefault(file, set()).update(
            int(n) for n in re.findall(r"(?m)^(\d+): ", sections[offset + 1])
        )
    return {file: sorted(numbers) for file, numbers in lines.items()}


def class_units(root: Path, production: str) -> list[Method]:
    """Complete, line-isolated fields and initializer blocks from buggy source only.

    These are repair units, never additional method-localization targets. Reject
    members sharing a line with a sibling or class boundary because canonical
    line edits cannot replace them without also replacing unrelated source.
    """
    units = []
    for path in sorted((root / production).rglob("*.java")):
        if path.is_symlink():
            raise ValueError("Source symlinks are forbidden")
        data = path.read_bytes()
        lines = data.decode().splitlines()
        byte_lines = data.splitlines()
        package = re.search(rb"\bpackage\s+([\w.]+)\s*;", data)
        prefix = package.group(1).decode() + "." if package else ""
        relative = path.relative_to(root).as_posix()
        for node in walk(PARSER.parse(data).root_node):
            if node.type not in {"field_declaration", "static_initializer", "block"}:
                continue
            if not node.parent or node.parent.type != "class_body":
                continue
            if not node.parent.parent or node.parent.parent.type not in CLASS_TYPES:
                continue
            start, end = node.start_point.row + 1, node.end_point.row + 1
            # Prefix/suffix must be whitespace: comments and sibling members stay untouched.
            if (
                byte_lines[start - 1][: node.start_point.column].strip()
                or byte_lines[end - 1][node.end_point.column :].strip()
            ):
                continue
            owners, parent = [], node.parent
            while parent:
                if parent.type in METHOD_TYPES:
                    owners = []
                    break
                if parent.type in CLASS_TYPES:
                    name = parent.child_by_field_name("name")
                    if name:
                        owners.append(data[name.start_byte : name.end_byte].decode())
                parent = parent.parent
            if not owners:
                continue
            owner = prefix + "$".join(reversed(owners))
            kind = "field" if node.type == "field_declaration" else "initializer"
            text = "\n".join(lines[start - 1 : end])
            calls = tuple(
                sorted(
                    {
                        data[n.start_byte : n.end_byte].decode()
                        for child in walk(node)
                        if child.type == "method_invocation"
                        and (n := child.child_by_field_name("name"))
                    }
                )
            )
            units.append(
                Method(
                    f"{relative}::{owner}#<{kind}>@{start}",
                    relative,
                    owner,
                    f"<{kind}>",
                    kind,
                    start,
                    end,
                    text,
                    calls,
                )
            )
    return units


def executable_lines(method: Method) -> list[int]:
    """Source-derived statement/condition starts; no spectra or gold locations."""
    return list(_executable_lines(method.text, method.start, method.end))


@lru_cache(maxsize=8192)
def _executable_lines(text: str, start: int, end: int) -> tuple[int, ...]:
    tree = PARSER.parse(("class __DebugUnit {\n" + text + "\n}").encode())
    return tuple(
        sorted(
            {
                start + node.start_point.row - 1
                for node in walk(tree.root_node)
                if (node.type.endswith("_statement") or node.type == "local_variable_declaration")
                and start <= start + node.start_point.row - 1 <= end
            }
        )
    )
