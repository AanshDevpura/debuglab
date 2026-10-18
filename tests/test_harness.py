from pathlib import Path

import pytest

from debuglab.harness import sanitize


def test_sanitize_removes_vcs_and_patches(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "history").write_text("secret")
    (tmp_path / "x.patch").write_text("secret")
    (tmp_path / "Source.java").write_text("class Source {}")
    sanitize(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["Source.java"]


def test_pipeline_cannot_import_gold() -> None:
    import ast

    for path in Path("src/debuglab").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("gold" not in alias.name for alias in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert "gold" not in (node.module or ""), path


@pytest.mark.integration
def test_real_bug_checkout_and_trigger(tmp_path: Path) -> None:
    import os

    from debuglab.config import Config
    from debuglab.harness import Harness, failing_count

    if os.environ.get("DEBUGLAB_INTEGRATION") != "1":
        pytest.skip("Set DEBUGLAB_INTEGRATION=1 after installing real Defects4J")
    from dataclasses import replace

    config = replace(
        Config.load(Path("tests/fixtures/config.json")), work_root=str(tmp_path / "bugs")
    )
    root = Path(config.work_root) / "Lang-1"
    if root.exists():
        pytest.fail("Integration requires a fresh checkout")
    harness = Harness(config, "Lang-1", Path("work/integration-logs"))
    harness.checkout()
    assert harness.compile().returncode == 0
    triggers = harness.export("tests.trigger").splitlines()
    assert triggers
    assert failing_count(harness.test(triggers[0])) > 0
    assert not list(root.rglob(".git"))
    assert not list(root.rglob("*.patch"))
