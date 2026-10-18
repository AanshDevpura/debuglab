from pathlib import Path

from debuglab.failure import compact


def test_compact_retains_messages_frames_and_trigger_associations() -> None:
    error = (
        "--- p.Test::test\njava.lang.AssertionError: expected 2 but was 1\n"
        "\tat p.A.f(A.java:3)\n\tat p.A$1.run(A.java:4)\n"
        "\tat java.lang.reflect.Method.invoke(Method.java:8)\n"
        "\tat java.base/java.lang.reflect.Method.invoke(Method.java:8)\n"
    )
    failure = {
        "triggers": ["p.Test::test", "p.Test::other"],
        "errors": [{"test": test, "error": error} for test in ["p.Test::test", "p.Test::other"]],
    }
    text, omissions = compact(failure, frozenset({"p.A"}))
    assert text["exception_and_assertion_messages"] == [
        "java.lang.AssertionError: expected 2 but was 1"
    ]
    assert text["project_frames"] == ["at p.A.f(A.java:3)", "at p.A$1.run(A.java:4)"]
    assert [row["test"] for row in text["failures"]] == failure["triggers"]
    assert all(row["message_ids"] == [0] for row in text["failures"])
    assert all(row["project_frame_ids"] == [0, 1] for row in text["failures"])
    assert {row["reason"] for row in omissions} == {
        "test_header_already_recorded_or_blank",
        "non_project_stack_frame",
        "repeated_diagnostic_replaced_by_reference",
    }


def test_inherited_trigger_source_resolves_imported_base_and_original_lines(
    tmp_path: Path,
) -> None:
    from debuglab.failure import test_hierarchy

    sources = tmp_path / "tests"
    (sources / "p").mkdir(parents=True)
    (sources / "base").mkdir()
    child = sources / "p/Child.java"
    child.write_text("package p;\nimport base.Base;\npublic class Child extends Base {}\n")
    (sources / "base/Base.java").write_text(
        "package base;\npublic class Base {\n"
        "  public void testInherited() { checkValue(); }\n"
        "  protected void setUp() { makeFixture(); }\n}\n"
    )
    hierarchy = test_hierarchy(child, "p.Child", tmp_path, sources)
    assert [file.name for file, _, _ in hierarchy] == ["Child.java", "Base.java"]
    methods = hierarchy[1][1]
    test = next(m for m in methods if m.name == "testInherited")
    assert test.class_name == "base.Base" and test.start == 3
    assert test.numbered().startswith("3: ") and test.calls == ("checkValue",)
    assert any(m.name == "setUp" for m in methods)
