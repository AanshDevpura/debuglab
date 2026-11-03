"""Independent ranking and source-only dependency allocation contracts."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from debuglab.config import Config
from debuglab.dependencies import same_class_dependencies
from debuglab.interface import MethodInterface
from debuglab.retrieval import build
from debuglab.source import parse_file


@pytest.fixture
def methods(tmp_path):
    source = tmp_path / "A.java"
    source.write_text("""class A {
 int caller() {
  return helper();
 }
 int unrelated() {
  return 7;
 }
 int helper() {
  return 1;
 }
}""")
    return parse_file(source, tmp_path)


def test_independent_global_statements_do_not_follow_method_blocks(methods):
    units, _ = methods
    context = "\n".join("METHOD " + m.id + "\n" + m.numbered() for m in units)
    api = MethodInterface(context, units, independent_statements=True)
    data = {
        "methods": [{"method_id": "M001"}],
        "statements": [{"method_id": "M003", "line": 9}, {"method_id": "M001", "line": 3}],
    }
    result = api.localization(json.dumps(data))
    assert result["parse_error"] is None
    assert result["method"] == [{"id": units[0].id, "rank": 1}]
    assert result["statement"] == [{"id": "A.java:9", "rank": 1}, {"id": "A.java:3", "rank": 2}]
    assert api.schema("localize")["required"] == ["methods", "statements"]
    assert "Statement order is global" in api.prompt("localize", None)[1]["content"]
    for bad in [2, 4, True, 99]:
        data["statements"] = [{"method_id": "M001", "line": bad}]
        assert api.localization(json.dumps(data))["parse_error"]
    data["statements"] = [{"method_id": "M003", "line": 9}] * 2
    assert api.localization(json.dumps(data))["parse_error"]


def test_dependencies_are_same_class_and_unambiguous(methods):
    units, _ = methods
    graph = same_class_dependencies(units)
    assert graph[units[0].id] == [units[2].id]
    other = replace(units[2], id="other", class_name="Other")
    assert same_class_dependencies([units[0], other])[units[0].id] == []
    overload = replace(units[2], id="overload", signature="int helper(String x)")
    assert same_class_dependencies(units + [overload])[units[0].id] == []


def test_dependency_allocation_is_budgeted_shared_and_frozen(methods, tmp_path):
    units, skeleton = methods
    cfg = replace(
        Config.load(Path("tests/fixtures/config.json")),
        retrieval_candidates=1,
        dependency_context=True,
        independent_statements=True,
    )
    failure = {
        "test_source": ["caller();"],
        "errors": [],
        "frames": [],
        "called_names": ["caller"],
        "hash": "failure",
    }

    class Budget:
        input_limit = 100000

        def count(self, text):
            return len(text)

        def messages_count(self, messages):
            return sum(len(m["content"]) for m in messages)

    out = tmp_path / "out"
    out.mkdir()
    manifest = build(units, {"A.java": skeleton}, failure, frozenset(), cfg, Budget(), out)
    assert manifest["method_ids"] == [units[0].id, units[2].id]
    assert manifest["dependency_context"][0]["included"]
    assert not any(r["method_id"] == units[2].id for r in manifest["omitted"])
    context = (out / "context.txt").read_text()
    api = MethodInterface(context, units, independent_statements=True)
    for task in ["localize", "repair"]:
        for hint in [None, {"methods": [{"id": units[2].id, "rank": 1}], "statements": []}]:
            assert api.prompt(task, hint)[1]["content"].startswith(context)
    assert build(units, {"A.java": skeleton}, failure, frozenset(), cfg, Budget(), out) == manifest
    (out / "context.txt").write_text("changed")
    with pytest.raises(RuntimeError, match="Frozen context"):
        build(units, {"A.java": skeleton}, failure, frozenset(), cfg, Budget(), out)


def test_helper_is_omitted_when_reserved_prompt_would_exceed_budget(methods, tmp_path):
    units, skeleton = methods
    cfg = replace(
        Config.load(Path("tests/fixtures/config.json")),
        retrieval_candidates=1,
        dependency_context=True,
    )

    class Budget:
        input_limit = 100000

        def count(self, text):
            return len(text)

        def messages_count(self, messages):
            text = "".join(m["content"] for m in messages)
            return self.input_limit + 1 if "METHOD " + units[2].id in text else len(text)

    out = tmp_path / "limited"
    out.mkdir()
    failure = {
        "test_source": ["caller();"],
        "errors": [],
        "frames": [],
        "called_names": ["caller"],
        "hash": "failure",
    }
    manifest = build(units, {"A.java": skeleton}, failure, frozenset(), cfg, Budget(), out)
    assert manifest["method_ids"] == [units[0].id]
    assert manifest["dependency_context"] == [
        {"caller": units[0].id, "callee": units[2].id, "included": False, "reason": "budget"}
    ]


def test_study_configs_and_inactive_historical_fields_are_supported(tmp_path):
    qwen = Config.load(Path("configs/study-qwen.json"))
    gpt = Config.load(Path("configs/study-gpt54-mini.json"))
    assert qwen.independent_statements and qwen.dependency_context
    assert qwen.strict_method_replacements and gpt.strict_method_replacements
    assert qwen.bugs_path == gpt.bugs_path
    data = json.loads(Path("configs/study-qwen.json").read_text())
    data.update(agentic=False, phase_agents=False, baseline_root=None)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    assert Config.load(path) == qwen
    data["agentic"] = True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unsupported experimental setting: agentic"):
        Config.load(path)
