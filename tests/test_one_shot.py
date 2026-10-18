import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from debuglab.config import Config
from debuglab.edits import apply, parse
from debuglab.interface import MethodInterface
from debuglab.retrieval import balanced_order, build
from debuglab.source import Method, class_units, context_lines, executable_lines, parse_file


def source(tmp_path: Path) -> tuple[Path, list[Method], list[Method]]:
    path = tmp_path / "src/Example.java"
    path.parent.mkdir()
    path.write_text(
        "class Example {\n"
        "  static int count = 1;\n"
        "  static {\n"
        "    count += 1;\n"
        "  }\n"
        "  { count += 2; }\n"
        "  int f(int value) {\n"
        "    // a declaration is not executable\n"
        "    if (value > 0) {\n"
        "      return count;\n"
        "    }\n"
        "    return 0;\n"
        "  }\n"
        "}\n"
    )
    methods, _ = parse_file(path, tmp_path)
    return path, methods, class_units(tmp_path, "src")


def test_class_units_keep_original_ranges_and_method_metrics(tmp_path: Path) -> None:
    path, methods, units = source(tmp_path)
    assert len(methods) == 1
    assert [(u.start, u.end) for u in units] == [(2, 2), (3, 5), (6, 6)]
    for unit in units:
        assert unit.text == "\n".join(path.read_text().splitlines()[unit.start - 1 : unit.end])
        assert unit.id not in {m.id for m in methods}
    context = "\n".join(
        [f"METHOD {m.id}\n{m.numbered()}" for m in methods]
        + [f"SOURCE UNIT {u.id}\n{u.numbered()}" for u in units]
    )
    api = MethodInterface(context, methods, source_units=units)
    repair_handles = api.schema("repair")["properties"]["edits"]["items"]["properties"][
        "method_id"
    ]["enum"]
    assert repair_handles == ["M001", "U001", "U002", "U003"]
    assert api.localization('{"methods":[{"method_id":"U001","lines":[2]}]}')["parse_error"]
    assert context_lines(context)["src/Example.java"] == list(range(2, 14))


def test_class_units_reject_shared_lines_local_and_anonymous_members(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/A.java").write_text(
        "class A { int x;\n"
        '  String a = "é"; int b;\n'
        "  int safe;\n"
        "  void f() {\n"
        "    class Local {\n"
        "      int local;\n"
        "    }\n"
        "  }\n"
        "  Object o = new Object() {\n"
        "    int anonymous;\n"
        "  };\n"
        "}\n"
    )
    units = class_units(tmp_path, "src")
    assert [u.start for u in units] == [3, 9]


def test_executable_candidates_exclude_declarations_comments_and_braces(tmp_path: Path) -> None:
    _, methods, _ = source(tmp_path)
    method = methods[0]
    assert executable_lines(method) == [9, 10, 12]
    context = f"METHOD {method.id}\n{method.numbered()}"
    api = MethodInterface(context, methods, executable_line_hints=True)
    row = api.schema("localize")["properties"]["methods"]["items"]["oneOf"][0]
    assert row["properties"]["lines"]["items"]["enum"] == [9, 10, 12]
    assert api.localization('{"methods":[{"method_id":"M001","lines":[7]}]}')["parse_error"]
    assert (
        api.localization('{"methods":[{"method_id":"M001","lines":[9]}]}')["statement"][0]["id"]
        == "src/Example.java:9"
    )


def test_field_only_context_has_valid_empty_method_schema(tmp_path: Path) -> None:
    _, _, units = source(tmp_path)
    context = f"SOURCE UNIT {units[0].id}\n{units[0].numbered()}"
    api = MethodInterface(context, [], source_units=units, executable_line_hints=True)
    rows = api.schema("localize")["properties"]["methods"]
    assert rows["maxItems"] == 0
    assert rows["items"] == {"type": "object"}
    assert api.localization('{"methods":[]}')["parse_error"] is None
    assert api.schema("repair")["properties"]["edits"]["items"]["properties"]["method_id"][
        "enum"
    ] == ["U001"]


def test_balanced_order_preserves_evidence_tiers_and_file_coverage() -> None:
    methods = [
        Method(key, file, "A", "f", "void f()", 1, 1, "", ())
        for key, file in [("a1", "A.java"), ("a2", "A.java"), ("b", "B.java"), ("stack", "C.java")]
    ]
    reasons = {
        m.id: {"executed": True, "test_call": False, "stack_frame": m.id == "stack"}
        for m in methods
    }
    assert balanced_order(["a1", "a2", "b", "stack"], {m.id: m for m in methods}, reasons) == [
        "stack",
        "a1",
        "b",
        "a2",
    ]


class CharacterBudget:
    input_limit = 15000

    def count(self, text: str) -> int:
        return len(text)

    def messages_count(self, messages: list[dict]) -> int:
        return sum(len(m["content"]) for m in messages)


def test_retrieval_freezes_shared_context_and_separate_unit_inventory(tmp_path: Path) -> None:
    _, methods, units = source(tmp_path)
    config = replace(
        Config.load(Path("tests/fixtures/config.json")),
        method_interface=True,
        balanced_retrieval=True,
        class_unit_edits=True,
        executable_line_hints=True,
    )
    failure = {
        "test_source": ["assert f(1) == 2;"],
        "errors": [],
        "frames": [],
        "called_names": ["f"],
        "hash": "original-failure",
    }
    output = tmp_path / "output"
    output.mkdir()
    _, skeleton = parse_file(tmp_path / "src/Example.java", tmp_path)
    budget = CharacterBudget()
    manifest = build(
        methods,
        {"src/Example.java": skeleton},
        failure,
        frozenset({methods[0].id}),
        config,
        budget,
        output,
        units,
    )
    assert manifest["method_ids"] == [methods[0].id]
    assert set(manifest["source_unit_ids"]) == {u.id for u in units}
    context = (output / "context.txt").read_text()
    assert context.count("static int count = 1;") == 1
    api = MethodInterface(context, methods, source_units=units, executable_line_hints=True)
    for task in ["localize", "repair"]:
        for hint in [None, {"methods": [{"id": methods[0].id, "rank": 1}], "statements": []}]:
            messages = api.prompt(task, hint)
            assert messages[1]["content"].startswith(context + "\n\nLOCALIZATION HINT:\n")
            assert budget.messages_count(messages) <= budget.input_limit


@pytest.mark.integration
def test_class_unit_patch_compiles_without_rewriting_other_members(tmp_path: Path) -> None:
    path, methods, units = source(tmp_path)
    context = (
        f"METHOD {methods[0].id}\n{methods[0].numbered()}\n"
        f"SOURCE UNIT {units[0].id}\n{units[0].numbered()}"
    )
    api = MethodInterface(context, methods, source_units=units)
    raw = json.dumps({"edits": [{"method_id": "U001", "replacement": "  static int count = 7;"}]})
    edits = parse(api.edits(raw))
    assert (edits[0].start_line, edits[0].end_line) == (2, 2)
    before = path.read_text()
    apply(tmp_path, edits, "src", "test")
    assert path.read_text() == before.replace("static int count = 1;", "static int count = 7;")
    compiled = subprocess.run(["javac", str(path)], capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stderr
