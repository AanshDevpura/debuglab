"""Deterministic method handles and schema-constrained original-source mappings."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from debuglab.edits import Edit
from debuglab.localization import parse
from debuglab.model import messages
from debuglab.source import Method, executable_lines


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject duplicate JSON keys instead of silently retaining their last value."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class MethodInterface:
    def __init__(
        self,
        context: str,
        methods: list[Method],
        hint_limit: int = 30,
        line_array_edits: bool = False,
        java_source_guidance: bool = False,
        source_units: list[Method] | None = None,
        executable_line_hints: bool = False,
    ):
        self.hint_limit = hint_limit
        self.line_array_edits = line_array_edits
        self.java_source_guidance = java_source_guidance
        self.executable_line_hints = executable_line_hints
        by_id = {m.id: m for m in methods}
        ids = re.findall(r"(?m)^METHOD (.+)$", context)
        self.units = {f"M{i:03d}": by_id[mid] for i, mid in enumerate(ids, 1)}
        by_unit = {m.id: m for m in source_units or []}
        unit_ids = re.findall(r"(?m)^SOURCE UNIT (.+)$", context)
        self.repair_units = dict(self.units)
        self.repair_units.update({f"U{i:03d}": by_unit[mid] for i, mid in enumerate(unit_ids, 1)})
        self.context = context

    def hint(self, hint: dict | None) -> dict | None:
        if hint is None:
            return None
        handles = {m.id: key for key, m in self.units.items()}
        locations = {
            f"{m.file}:{n}" for m in self.repair_units.values() for n in range(m.start, m.end + 1)
        }
        return {
            "methods": [
                dict(row, id=handles[row["id"]]) for row in hint["methods"] if row["id"] in handles
            ][: self.hint_limit],
            "statements": [row for row in hint["statements"] if row["id"] in locations][
                : self.hint_limit
            ],
        }

    def prompt(self, task: str, hint: dict | None) -> list[dict]:
        # Context bytes remain identical in every condition. Only the hint varies.
        prompt = messages(self.context, task, self.hint(hint))
        mapping = "\n".join(
            f"{key} = {m.id} (original lines {m.start}..{m.end})"
            for key, m in (self.units if task == "localize" else self.repair_units).items()
        )
        instruction = (
            "Rank at most 10 methods by cause of the observed failure, most suspicious first. "
            'Return {"methods":[{"method_id":"M001","lines":[123]}]}. '
            "Use the short handles below. Select at most 5 original numbered lines per method, "
            "most suspicious first. Never repeat a method or a line. A line must lie inside "
            "that method. Return fewer methods and lines if uncertain. "
            "Do not rank a detector merely because it appears in a failing assertion."
            if task == "localize"
            else "Repair the cause of the observed failures. Return "
            '{"edits":[{"method_id":"M001","replacement":"complete Java method here"}]}. '
            "Use 1 to 3 different short method handles below. Each replacement must be the "
            "COMPLETE method or constructor, including its original declaration, arguments, "
            "opening brace, full corrected body, and closing brace. The harness replaces "
            "exactly that original method, mapping its handle to file and inclusive line range. "
            "Retain the original API and unrelated behavior. Preserve unchanged statements "
            "inside that method. Never copy test assertions into production. Do not output "
            "numbers before code, edit coordinates, diffs, ellipses, or omitted-code comments. "
            "Read the failing test expectations and explain them through production behavior "
            "internally before making the smallest semantic change."
        )
        if task == "localize" and self.executable_line_hints:
            instruction += (
                " Choose lines only from these AST statement/condition starts; declaration, "
                "comment and brace-only lines are not candidates. Methods without executable "
                "statements must have an empty lines array.\n"
                + "\n".join(f"{key}: {executable_lines(m)}" for key, m in self.units.items())
            )
        if task == "repair" and len(self.repair_units) > len(self.units):
            instruction += (
                " U handles name complete field declarations or initializer blocks, not methods. "
                "For a U handle replace exactly that entire supplied declaration/block, preserving "
                "its scope and unrelated behavior. Do not add a surrounding class or method. "
                "The shared key method_id accepts either M or U handles; all other edit rules "
                "and the maximum of 3 edits still apply."
            )
        if task == "repair" and self.line_array_edits:
            instruction = instruction.replace(
                '"replacement":"complete Java method here"',
                '"replacement_lines":["complete declaration and opening brace",'
                '"all body lines","closing brace"]',
            ).replace(
                "Each replacement must be the", "Each replacement_lines array must encode the"
            )
            instruction += (
                " Each array item is one unnumbered Java source line. Include every line of "
                "the complete corrected method, from declaration through its final closing brace. "
                "The harness joins lines exactly with newline separators into one replacement. "
                "Never split one method across multiple edit blocks."
            )
        if task == "repair" and self.java_source_guidance:
            instruction += (
                " Check every method call against the supplied source; do not assume newer "
                "library APIs. Any new helper you call needs an implementation in your edits. "
                "Respect the existing Java types and source style. For raw collections, use "
                "indexed access with explicit casts instead of assuming generic element types."
            )
        prefix = prompt[1]["content"].split("\n\nTASK:\n", 1)[0]
        prompt[1]["content"] = (
            prefix + "\n\nTASK:\n" + instruction + "\nMETHOD HANDLES:\n" + mapping
        )
        return prompt

    def schema(self, task: str) -> dict:
        if task == "localize":
            rows = [
                {
                    "type": "object",
                    "properties": {
                        "method_id": {"const": key},
                        "lines": {
                            "type": "array",
                            "items": (
                                {"type": "integer", "enum": self.lines(m)}
                                if self.lines(m)
                                else {"type": "integer"}
                            ),
                            "maxItems": min(5, len(self.lines(m))),
                            "uniqueItems": True,
                        },
                    },
                    "required": ["method_id", "lines"],
                    "additionalProperties": False,
                }
                for key, m in self.units.items()
            ]
            name, minimum, maximum, item = (
                "methods",
                0,
                10 if rows else 0,
                {"oneOf": rows} if rows else {"type": "object"},
            )
        else:
            name, minimum, maximum = "edits", 1, 3
            item = {
                "type": "object",
                "properties": {
                    "method_id": {"type": "string", "enum": list(self.repair_units)},
                    "replacement": {"type": "string"},
                },
                "required": ["method_id", "replacement"],
                "additionalProperties": False,
            }
        if task == "repair" and self.line_array_edits:
            item["properties"].pop("replacement")
            item["properties"]["replacement_lines"] = {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            }
            item["required"] = ["method_id", "replacement_lines"]
        return {
            "type": "object",
            "properties": {
                name: {"type": "array", "items": item, "minItems": minimum, "maxItems": maximum}
            },
            "required": [name],
            "additionalProperties": False,
        }

    def localization(self, raw: str) -> dict:
        try:
            data = json.loads(raw, object_pairs_hook=unique_object)
            if not isinstance(data, dict) or set(data) != {"methods"}:
                raise ValueError("Expected methods object")
            if not isinstance(data["methods"], list) or len(data["methods"]) > 10:
                raise ValueError("Expected at most 10 methods")
            for row in data["methods"]:
                if not isinstance(row, dict) or set(row) != {"method_id", "lines"}:
                    raise ValueError("Incorrect localization fields")
                method = self.units[row["method_id"]]
                row["method_id"] = method.id
                if not isinstance(row["lines"], list) or len(row["lines"]) > 5:
                    raise ValueError("Expected at most five lines")
                if any(
                    type(line) is not int or line not in self.lines(method) for line in row["lines"]
                ):
                    raise ValueError("Line is not an allowed source candidate")
            return parse(
                json.dumps(data), list(self.units.values()), {m.id for m in self.units.values()}
            )
        except (ValueError, TypeError, KeyError) as error:
            return {"method": [], "statement": [], "parse_error": str(error)}

    def edits(self, raw: str) -> str:
        data = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(data, dict) or set(data) != {"edits"}:
            raise ValueError("Expected edits object")
        if not isinstance(data["edits"], list) or not 1 <= len(data["edits"]) <= 3:
            raise ValueError("Expected 1 to 3 methods")
        canonical, seen = [], set()
        replacement_key = "replacement_lines" if self.line_array_edits else "replacement"
        for row in data["edits"]:
            if not isinstance(row, dict) or set(row) != {"method_id", replacement_key}:
                raise ValueError("Incorrect method edit fields")
            key = row["method_id"]
            replacement = row[replacement_key]
            if self.line_array_edits:
                if (
                    not isinstance(replacement, list)
                    or not replacement
                    or any(
                        not isinstance(line, str) or "\n" in line or "\r" in line
                        for line in replacement
                    )
                ):
                    raise ValueError("Expected a nonempty array of individual source lines")
                replacement = "\n".join(replacement)
            if key in seen or not isinstance(replacement, str):
                raise ValueError("Duplicate method or nontext replacement")
            seen.add(key)
            method = self.repair_units[key]
            canonical.append(asdict(Edit(method.file, method.start, method.end, replacement)))
        return json.dumps({"edits": canonical})

    def lines(self, method: Method) -> list[int]:
        return (
            executable_lines(method)
            if self.executable_line_hints
            else list(range(method.start, method.end + 1))
        )
