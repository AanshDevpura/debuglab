import sys
import time
from pathlib import Path

import pytest

from debuglab.io import run


@pytest.mark.parametrize("wall_jump", [-1000000, 1000000])
def test_subprocess_budget_ignores_wall_clock_jumps(tmp_path, monkeypatch, wall_jump):
    # The same monotonic deadline must permit real work after either calendar jump.
    monkeypatch.setenv("DEBUGLAB_BUG_DEADLINE_MONOTONIC", str(time.monotonic() + 2))
    monkeypatch.setenv("DEBUGLAB_BUG_DEADLINE", "0")  # obsolete wall deadline is ignored
    monkeypatch.setattr(time, "time", lambda: wall_jump)
    result = run([sys.executable, "-c", 'print("completed")'], tmp_path, tmp_path / "log.json")
    assert result.returncode == 0 and not result.timed_out
    assert result.output.strip() == "completed"


def test_subprocess_honors_fractional_remaining_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUGLAB_BUG_DEADLINE_MONOTONIC", str(time.monotonic() + 0.1))
    result = run(
        [sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, tmp_path / "log.json"
    )
    assert result.timed_out and result.seconds < 2


def test_worker_restores_deadline_environment_after_failure(tmp_path, monkeypatch):
    from dataclasses import asdict, replace

    import debuglab.pipeline
    from debuglab.config import Config
    from debuglab.io import save
    from debuglab.study import one

    config = replace(
        Config.load(Path("tests/fixtures/config.json")),
        split_path=str(tmp_path / "split.json"),
        output_root=str(tmp_path / "runs"),
    )
    save(Path(config.split_path), {"dev": ["Lang-1"]})
    config_path = tmp_path / "config.json"
    save(config_path, asdict(config))
    monkeypatch.setenv("DEBUGLAB_BUG_DEADLINE_MONOTONIC", "old-parent-value")
    monkeypatch.setattr("debuglab.model.TokenBudget", lambda model: None)

    def fail(*args):
        import os

        remaining = float(os.environ["DEBUGLAB_BUG_DEADLINE_MONOTONIC"]) - time.monotonic()
        assert 0 < remaining <= config.bug_timeout
        raise RuntimeError("pipeline interrupted")

    monkeypatch.setattr(debuglab.pipeline, "run_bug", fail)
    with pytest.raises(RuntimeError, match="pipeline interrupted"):
        one(config_path, "dev", "Lang-1")
    import os

    assert os.environ["DEBUGLAB_BUG_DEADLINE_MONOTONIC"] == "old-parent-value"
