"""Guarded launch preparation must work on a fresh root without sending requests."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_guarded_runner_freezes_before_ledger_and_preserves_reservations(tmp_path, monkeypatch):
    source = Path.cwd()
    spec = importlib.util.spec_from_file_location("hosted_runner", source / "scripts/run-hosted.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "configs").mkdir()
    for name in ["study-gpt54-mini.json", "bugs-study-100.json"]:
        (tmp_path / "configs" / name).write_bytes((source / "configs" / name).read_bytes())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("sys.argv", ["run-hosted.py", "--output", "outputs/test-guard"])
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: None)
    dispatched = []

    def start(command, **kwargs):
        root = tmp_path / "outputs/test-guard"
        assert (root / "config.json").exists()
        assert (root / "run-fingerprint.json").exists()
        assert kwargs["env"]["DEBUGLAB_HOSTED_SPENDING_ROOT"] == str(root)
        dispatched.append(command)
        return SimpleNamespace(poll=lambda: 0, returncode=0)

    monkeypatch.setattr(module.subprocess, "Popen", start)
    with pytest.raises(SystemExit) as ended:
        module.main()
    assert ended.value.code == 0 and len(dispatched) == 1
    ledger = tmp_path / "outputs/test-guard/spending-ledger.json"
    record = json.loads(ledger.read_text())
    record["reserved_micro_usd"] = 123
    ledger.write_text(json.dumps(record))
    with pytest.raises(SystemExit):
        module.main()
    assert json.loads(ledger.read_text())["reserved_micro_usd"] == 123
