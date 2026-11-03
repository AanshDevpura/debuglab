import json
from pathlib import Path

import pytest

from debuglab.config import Config
from debuglab.interface import MethodInterface
from debuglab.method_contract import validate_replacement
from debuglab_gold.interface_readiness import reference_replacements, reverse_changes


@pytest.mark.parametrize(
    "replacement",
    [
        "public int f(int a) { return a + 1; }",
        "public int f(int renamed) { return renamed + 1; }",
        "public /* same declaration */ int f ( int a ) { return a + 1; }",
    ],
)
def test_preserve_api_without_whitespace_normalizing_output(replacement):
    validate_replacement("public int f(int a) { return a; }", replacement)


@pytest.mark.parametrize(
    "replacement",
    [
        "private int f(int a) { return a; }",
        "public static int f(int a) { return a; }",
        "public long f(int a) { return a; }",
        "public int f(long a) { return 1; }",
        "public int g(int a) { return a; }",
        "public int f(int a) { return a; } int helper() { return 1; }",
        "public int f(int a) { return a; } int field;",
        "public int f(int a) { return a; } } class Injected {} class More {",
        "public int f(int a) { return",
    ],
)
def test_reject_api_changes_or_extra_members(replacement):
    with pytest.raises(ValueError):
        validate_replacement("public int f(int a) { return a; }", replacement)


def test_constructor_and_throws_preserved():
    validate_replacement(
        "public A() throws Exception {}", "public A() throws Exception { int x=1; }"
    )
    with pytest.raises(ValueError):
        validate_replacement("public A() throws Exception {}", "public A() {}")


def method(tmp_path, text="class A {\n public int f() {\n  return 1;\n }\n}\n"):
    from debuglab.source import parse_file

    file = tmp_path / "A.java"
    file.write_text(text)
    return parse_file(file, tmp_path)[0]


def test_guard_is_versioned_and_preserves_valid_bytes(tmp_path):
    m = method(tmp_path)[0]
    api = MethodInterface(
        "METHOD " + m.id + "\n" + m.numbered(), [m], strict_method_replacements=True
    )
    replacement = " public int f() {\n  return 2;\n }"
    raw = json.dumps({"edits": [{"method_id": "M001", "replacement": replacement}]})
    assert json.loads(api.edits(raw))["edits"][0]["replacement"] == replacement
    bad = json.dumps(
        {"edits": [{"method_id": "M001", "replacement": "private int f() {return 2;}"}]}
    )
    with pytest.raises(ValueError):
        api.edits(bad)
    api.strict_method_replacements = False
    assert json.loads(api.edits(bad))["edits"][0]["replacement"].startswith("private")


def test_reverse_patch_exactly_replaces_buggy_method(tmp_path):
    methods = method(tmp_path)
    patch = """--- a/A.java
+++ b/A.java
@@ -1,5 +1,5 @@
 class A {
  public int f() {
-  return 2;
+  return 1;
  }
 }
"""
    changed = reference_replacements(methods, reverse_changes(patch))
    assert changed == {methods[0].id: " public int f() {\n  return 2;\n }"}
    with pytest.raises(ValueError, match="differs"):
        reference_replacements(
            methods, reverse_changes(patch.replace("+  return 1;", "+  return 9;"))
        )


def test_class_scope_is_not_certified_as_method_fix(tmp_path):
    methods = method(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        reference_replacements(
            methods,
            [{"file": "A.java", "start": 1, "buggy": ["class A {"], "fixed": ["class B {"]}],
        )


def test_insertions_and_deletions_inside_methods(tmp_path):
    methods = method(tmp_path)
    patch = """--- a/A.java
+++ b/A.java
@@ -2,4 +2,3 @@
  public int f() {
-  int x = 2;
   return 1;
  }
"""
    assert "int x = 2;" in reference_replacements(methods, reverse_changes(patch))[methods[0].id]
    with pytest.raises(ValueError, match="outside"):
        reference_replacements(
            methods,
            [
                {
                    "file": "A.java",
                    "start": methods[0].end + 1,
                    "buggy": [],
                    "fixed": ["int global;"],
                }
            ],
        )


def test_malformed_hunk_fails_closed():
    with pytest.raises(ValueError):
        reverse_changes("--- a/A.java\n+++ b/A.java\n@@ -1,3 +1,3 @@\n x\n")


def test_config_guard_must_be_boolean(tmp_path):
    cfg = json.loads(Path("tests/fixtures/config.json").read_text())
    cfg["strict_method_replacements"] = "yes"
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    with pytest.raises(ValueError, match="boolean"):
        Config.load(p)


def test_native_replacement_contract_executes(tmp_path):
    import shutil
    import subprocess

    from debuglab.edits import apply, parse

    if not shutil.which("javac"):
        pytest.skip("Native Java compiler unavailable")
    root = tmp_path / "src"
    root.mkdir()
    file = root / "A.java"
    file.write_text(
        "class A {\n public static int f() {\n  return 1;\n }\n"
        " public static void main(String[] a) { if(f()!=2) throw new Error(); }\n}\n"
    )
    from debuglab.source import parse_file

    methods, _ = parse_file(file, tmp_path)
    m = next(m for m in methods if m.name == "f")
    api = MethodInterface(
        "METHOD " + m.id + "\n" + m.numbered(), methods, strict_method_replacements=True
    )
    raw = json.dumps(
        {
            "edits": [
                {"method_id": "M001", "replacement": " public static int f() {\n return 2;\n }"}
            ]
        }
    )
    apply(tmp_path, parse(api.edits(raw)), "src", "test")
    subprocess.run(["javac", str(file)], check=True, capture_output=True, timeout=30)
    subprocess.run(["java", "-cp", str(root), "A"], check=True, capture_output=True, timeout=30)


def test_oracle_contains_only_buggy_source_and_budget_fail_closed(tmp_path):
    from debuglab_gold.interface_readiness import audit_bug

    folder = tmp_path / "Bug-1"
    folder.mkdir()
    m = method(folder)[0]
    (folder / "methods.json").write_text(json.dumps([m.record()]))
    context = (
        "FROZEN DEBUGGING CONTEXT\nObserved failure only\n\nCLASS SKELETON A.java\n"
        "class A\n\nMETHOD " + m.id + "\n" + m.numbered()
    )
    (folder / "context.txt").write_text(context)
    (folder / "skeletons.json").write_text(json.dumps({"A.java": "class A"}))
    (folder / "context-manifest.json").write_text(json.dumps({"method_ids": [m.id]}))
    patch = tmp_path / "fix.patch"
    patch.write_text("--- a/A.java\n+++ b/A.java\n@@ -3 +3 @@\n-  return 87654321;\n+  return 1;\n")
    cfg = Config.load(Path("tests/fixtures/config.json"))

    class Budget:
        exact = True
        input_limit = 10000

        def count(self, text):
            return len(text)

        def messages_count(self, messages):
            return sum(len(m["content"]) for m in messages)

    output = tmp_path / "diagnostic"
    row = audit_bug(folder, patch, cfg, Budget(), output)
    assert row["oracle_ready"]
    oracle = (output / "oracle-contexts/Bug-1/context.txt").read_text()
    assert "87654321" not in oracle
    assert "3:   return 1;" in oracle
    assert "ORACLE DIAGNOSTIC" in oracle
    too_small = Budget()
    too_small.input_limit = 1
    row = audit_bug(folder, patch, cfg, too_small, tmp_path / "too-small")
    assert not row["oracle_ready"]
    assert "input budget" in row["reason"]
    assert not (tmp_path / "too-small/oracle-contexts").exists()


def test_reference_overlapping_method_ranges_fail_closed():
    from debuglab.source import Method

    outer = Method("outer", "A.java", "A", "f", "void f()", 2, 8, "", ())
    inner = Method("inner", "A.java", "A$Local", "g", "void g()", 5, 7, "", ())
    with pytest.raises(ValueError, match="Overlapping"):
        reference_replacements(
            [outer, inner],
            [
                {"file": "A.java", "start": 3, "buggy": ["x"], "fixed": ["y"]},
                {"file": "A.java", "start": 6, "buggy": ["a"], "fixed": ["b"]},
            ],
        )


def test_no_newline_patch_not_certified():
    with pytest.raises(ValueError, match="byte-level"):
        reverse_changes(
            "--- a/A.java\n+++ b/A.java\n@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n"
        )
