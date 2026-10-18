import ast
import json
from pathlib import Path

from debuglab.localization import parse
from debuglab.source import Method
from debuglab_gold.evaluate import leak_violations, metric, recall


def test_retriever_cannot_access_scores_or_gold() -> None:
    source = Path("src/debuglab/retrieval.py").read_text()
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not any("spectrum" in name or "gold" in name for name in imported)
    assert "ochiai" not in source.lower()
    assert "classes.modified" not in source


def test_literal_added_line_leak_is_detected(tmp_path: Path) -> None:
    (tmp_path / "calls").mkdir()
    path = tmp_path / "calls/call.json"
    path.write_text(
        json.dumps({"parameters": {"messages": [{"content": "12: return corrected();"}]}})
    )
    assert leak_violations({"added_lines": ["return corrected();"]}, tmp_path)
    assert not leak_violations({"added_lines": ["return other();"]}, tmp_path)
    assert not leak_violations(
        {
            "added_lines": ["return corrected();"],
            "permitted_preexisting_lines": ["return corrected();"],
        },
        tmp_path,
    )


def test_parse_failures_empty_and_lines_validated() -> None:
    m = Method("id", "src/A.java", "A", "f", "void f()", 2, 4, "", ())
    assert parse("garbage", [m], {"id"})["method"] == []
    raw = json.dumps({"methods": [{"method_id": "id", "lines": [5]}]})
    assert parse(raw, [m], {"id"})["statement"] == []
    valid = json.dumps({"methods": [{"method_id": "id", "lines": [3]}]})
    assert parse(valid, [m], {"id"})["statement"] == [{"id": "src/A.java:3", "rank": 1}]


def test_insertion_only_line_ineligible_and_missing_not_removed() -> None:
    gold = {"files": ["src/A.java"], "methods": ["id"], "lines": []}
    manifest = {"files": [], "method_ids": [], "lines": {}, "omitted": [], "executed_methods": []}
    measurement = recall(gold, manifest)
    assert measurement["line_any"] is None
    assert measurement["method_any"] is False
    assert metric([], ["id"]) == {
        "eligible": True,
        "rank": None,
        "top1": False,
        "top3": False,
        "top5": False,
    }


def test_preexisting_permission_handles_indentation_without_allowing_novel_code(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from debuglab.config import Config
    from debuglab_gold.evaluate import permit_preexisting

    config = replace(
        Config.load(Path("tests/fixtures/config.json")), work_root=str(tmp_path / "bugs")
    )
    production = tmp_path / "bugs/Lang-1/src"
    production.mkdir(parents=True)
    (production / "A.java").write_text("class A {\n        int f() { return 1; }\n}\n")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "metadata.json").write_text('{"dir.src.classes":"src"}')
    (artifacts / "methods.json").write_text("[]")
    gold = {"added_lines": ["    int f() { return 1; }", "    int f() { return 2; }"], "lines": []}
    checked = permit_preexisting(config, "Lang-1", artifacts, gold)
    assert checked["permitted_preexisting_lines"] == ["    int f() { return 1; }"]


def test_preexisting_fragment_with_longer_comment_still_rejects_novel_content() -> None:
    from debuglab_gold.evaluate import preexists

    original = {"return instant; // unchanged original explanation"}
    assert preexists("    return instant; // unchanged original", original)
    assert not preexists("return instant + diff; // unchanged original", original)
    assert not preexists("return instant; // novel explanation", original)
