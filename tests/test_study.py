import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from debuglab.config import Config
from debuglab.io import run
from debuglab.study import cleanup
from debuglab_gold.evaluate import metric, recall


def test_overall_deadline_bounds_native_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEBUGLAB_BUG_DEADLINE", "1")
    result = run(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        tmp_path,
        tmp_path / "timeout.json",
        timeout=30,
    )
    assert result.timed_out
    assert result.seconds < 3
    assert json.loads((tmp_path / "timeout.json").read_text())["timed_out"]


def test_cleanup_refuses_checkout_roots_outside_work(tmp_path: Path) -> None:
    config = replace(
        Config.load(Path("tests/fixtures/config.json")),
        work_root=str(tmp_path),
        cleanup_checkouts=True,
    )
    with pytest.raises(ValueError, match="workspace work"):
        cleanup(config, "Lang-1")


def test_cleanup_preserves_native_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = replace(
        Config.load(Path("tests/fixtures/config.json")),
        work_root=str(tmp_path / "work/bugs"),
        output_root=str(tmp_path / "outputs/runs"),
        cleanup_checkouts=True,
    )
    monkeypatch.chdir(tmp_path)
    logs = tmp_path / "work/attempts/Lang-1/direct-0-logs"
    logs.mkdir(parents=True)
    payload = '{"returncode":0,"output":"Failing tests: 0"}'
    (logs / "00002-test.json").write_text(payload)
    cleanup(config, "Lang-1")
    archived = tmp_path / "outputs/runs/Lang-1/evaluation-logs/direct-0-logs/00002-test.json"
    assert archived.read_text() == payload
    assert not logs.exists()


def test_unmapped_class_scope_fault_stays_in_method_denominator() -> None:
    gold = {"files": ["src/A.java"], "methods": [], "lines": ["src/A.java:2"]}
    manifest = {
        "files": ["src/A.java"],
        "method_ids": [],
        "lines": {"src/A.java": [2]},
        "omitted": [],
        "executed_methods": [],
    }
    measured = recall(gold, manifest)
    assert measured["line_all"]
    assert not measured["method_any"]
    assert not measured["method_all"]
    assert metric([], []) == {
        "eligible": True,
        "rank": None,
        "top1": False,
        "top3": False,
        "top5": False,
    }
