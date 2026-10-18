import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from debuglab.cohort_run import coordinate, prepare_run
from debuglab.config import Config
from debuglab.io import save
from debuglab.selection import read_bugs


def setup_run(tmp_path, monkeypatch):
    config = Config.load(Path("tests/fixtures/config.json"))
    monkeypatch.chdir(tmp_path)
    save(Path("selection.json"), ["Cli-1", "Lang-14"])
    config = replace(
        config,
        bugs_path="selection.json",
        output_root="outputs/test/runs",
        work_root="work/test/bugs",
    )
    path = Path("settings.json")
    save(path, asdict(config))
    return path


def test_one_bug_is_allowed_anywhere_in_selection_and_frozen(tmp_path, monkeypatch):
    path = setup_run(tmp_path, monkeypatch)
    config, manifest = prepare_run(path, bug="Lang-14", output=Path("outputs/single"))
    assert read_bugs(Path(config.bugs_path)) == ["Lang-14"]
    assert read_bugs(Path("selection.json")) == ["Cli-1", "Lang-14"]
    original = manifest.read_bytes()
    assert prepare_run(path, bug="Lang-14", output=Path("outputs/single"))[0] == config
    assert manifest.read_bytes() == original
    with pytest.raises(RuntimeError, match="selection changed"):
        prepare_run(path, output=Path("outputs/single"))
    with pytest.raises(ValueError, match="not in the selected"):
        prepare_run(path, bug="Math-1", output=Path("outputs/other"))


def test_run_rejects_modified_source_settings_and_unowned_results(tmp_path, monkeypatch):
    path = setup_run(tmp_path, monkeypatch)
    prepare_run(path)
    d = json.loads(path.read_text())
    d["model"]["model"] = "a-different-model"
    save(path, d)
    with pytest.raises(RuntimeError, match="configuration or bug selection changed"):
        prepare_run(path)
    d["model"]["model"] = "debuglab-qwen-coder"
    save(path, d)
    Path("src").mkdir()
    Path("src/changed.py").write_text("# source version changed")
    with pytest.raises(RuntimeError, match="Frozen run source"):
        prepare_run(path)
    Path("outputs/unowned").mkdir()
    Path("outputs/unowned/result.json").write_text("private evidence")
    with pytest.raises(RuntimeError, match="unowned evidence"):
        prepare_run(path, output=Path("outputs/unowned"))
    with pytest.raises(ValueError, match="inside outputs"):
        prepare_run(path, output=Path("results/final"))


def test_one_runner_uses_all_bugs_then_resumes_without_new_work(tmp_path, monkeypatch):
    path = setup_run(tmp_path, monkeypatch)
    executed, reports = [], []

    def fake_execute(config_path):
        config = Config.load(config_path)
        ids = read_bugs(Path(config.bugs_path))
        executed.append(ids)
        for bug in ids:
            save(
                Path(config.output_root) / bug / "result.json",
                {"pipeline_complete": True, "evaluated": True},
            )

    monkeypatch.setattr("debuglab.cohort_run.execute", fake_execute)
    monkeypatch.setattr("debuglab.cohort_run.command", lambda args: reports.append(args))
    monkeypatch.setattr("debuglab.cohort_run.final_audit", lambda config: {"bugs": 2})
    coordinate(path)
    coordinate(path)
    assert executed == [["Cli-1", "Lang-14"]]
    assert len(reports) == 2  # reports can be rebuilt without generating new answers
    state = json.loads(Path("outputs/test/coordinator-state.json").read_text())
    assert state == {"stage": "complete", "evaluated": 2}


def test_an_existing_coordinator_lock_blocks_duplicate_workers(tmp_path, monkeypatch):
    import fcntl

    path = setup_run(tmp_path, monkeypatch)
    Path("outputs/test").mkdir(parents=True)
    with Path("outputs/test/coordinator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(
            "debuglab.cohort_run.execute", lambda *a: pytest.fail("duplicate worker started")
        )
        with pytest.raises(RuntimeError, match="Another coordinator"):
            coordinate(path)
