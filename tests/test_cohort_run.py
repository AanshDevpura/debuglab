import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from debuglab.cohort_run import final_audit, verify_ledger
from debuglab.config import Config
from debuglab.io import digest, save
from debuglab.study import one
from debuglab_gold.comparison import compare


def results(tmp_path: Path) -> tuple[Path, Path]:
    baseline, candidate = tmp_path / "old", tmp_path / "new"
    for root, context in [(baseline, "original"), (candidate, "rebuilt")]:
        folder = root / "Lang-1"
        folder.mkdir(parents=True)
        for name in ["failure.json", "spectrum.json", "source-hashes.json", "methods.json"]:
            (folder / name).write_text("identical source evidence")
        save(
            folder / "result.json",
            {
                "bug": "Lang-1",
                "evaluated": True,
                "pipeline_complete": True,
                "context_hash": context,
                "failure_hash": "unchanged",
                "localization": {
                    r: {
                        "metrics": {
                            level: {f"top{k}": False for k in [1, 3, 5]}
                            for level in ["method", "line"]
                        }
                    }
                    for r in ["spectrum", "llm", "combined"]
                },
                "repair": {
                    c: [{"status": "FAILS_TRIGGER"}]
                    for c in ["direct", "spectrum", "llm", "combined"]
                },
            },
        )
    return baseline, candidate


def test_context_comparison_is_explicit_and_checks_baseline_evidence(tmp_path: Path) -> None:
    old, new = results(tmp_path)
    with pytest.raises(ValueError, match="changed frozen inputs"):
        compare(old, new, ["Lang-1"], tmp_path / "comparison.json")
    result = compare(old, new, ["Lang-1"], tmp_path / "comparison.json", allow_context_changes=True)
    assert not result["identical_frozen_inputs"]
    assert result["changed_context_bugs"] == ["Lang-1"]
    assert result["comparison_scope"] == "source-context-and-interface"
    (new / "Lang-1/spectrum.json").write_text("different coverage")
    with pytest.raises(ValueError, match="changed baseline evidence"):
        compare(old, new, ["Lang-1"], tmp_path / "comparison.json", allow_context_changes=True)


def test_run_fingerprint_blocks_changed_source_before_any_model_call(tmp_path: Path) -> None:
    config = replace(
        Config.load(Path("tests/fixtures/config.json")),
        split_path=str(tmp_path / "split.json"),
        output_root=str(tmp_path / "runs"),
    )
    save(Path(config.split_path), {"dev": ["Lang-1"]})
    path = tmp_path / "config.json"
    save(path, asdict(config))
    save(tmp_path / "run-fingerprint.json", {"different": "source"})
    with pytest.raises(RuntimeError, match="Run code/config/split changed"):
        one(path, "dev", "Lang-1")
    assert not (tmp_path / "runs/Lang-1/calls").exists()


def test_protected_evidence_and_incomplete_audit_fail_closed(tmp_path: Path) -> None:
    evidence = tmp_path / "saved.json"
    evidence.write_text("original")
    save(tmp_path / "baseline-artifact-hashes.json", {str(evidence): digest(evidence.read_bytes())})
    verify_ledger(tmp_path)
    evidence.write_text("changed")
    with pytest.raises(RuntimeError, match="Protected baseline evidence"):
        verify_ledger(tmp_path)
    config = replace(
        Config.load(Path("tests/fixtures/config.json")),
        split_path=str(tmp_path / "split.json"),
        output_root=str(tmp_path / "runs"),
    )
    save(Path(config.split_path), {"pool": ["Lang-1"]})
    save(tmp_path / "runs/Lang-1/result.json", {"pipeline_complete": False})
    with pytest.raises(RuntimeError, match="Incomplete bug"):
        final_audit(config, tmp_path / "old")


def test_final_audit_distinguishes_exact_replay_from_changed_context(tmp_path: Path) -> None:
    config = replace(
        Config.load(Path("tests/fixtures/config.json")),
        output_root=str(tmp_path / "runs"),
        work_root=str(tmp_path / "work/bugs"),
        split_path=str(tmp_path / "split.json"),
    )
    save(Path(config.split_path), {"pool": ["Lang-1"]})
    candidate, baseline = Path(config.output_root) / "Lang-1", tmp_path / "old/Lang-1"
    for folder in (candidate, baseline):
        folder.mkdir(parents=True)
        for name in ("failure.json", "spectrum.json", "source-hashes.json", "methods.json"):
            save(folder / name, {})
        (folder / "context.txt").write_text("original")
    save(candidate / "context-manifest.json", {})
    save(
        candidate / "result.json",
        {
            "evaluated": True,
            "pipeline_complete": True,
            "leak_violations": [],
            "context_hash": digest(b"original"),
            "failure_hash": digest((candidate / "failure.json").read_bytes()),
            "repair": {
                k: [{"status": "FAILS_TRIGGER"}] for k in ("direct", "spectrum", "llm", "combined")
            },
        },
    )
    for index in range(6):
        save(
            candidate / "calls" / f"{index}.json",
            {
                "status": "OK",
                "context_window": 16384,
                "input_tokens_exact": 20,
                "parameters": {
                    "max_tokens": 2048,
                    "messages": [
                        {"content": "system"},
                        {"content": "original\n\nLOCALIZATION HINT:\nnone"},
                    ],
                },
            },
        )
    assert not final_audit(config, baseline.parent)["contexts_rebuilt"]
    (baseline / "context.txt").write_text("different baseline")
    assert final_audit(config, baseline.parent)["contexts_rebuilt"]
    # The audit remains read-only even when it reports the changed context.
    assert json.loads((candidate / "result.json").read_text())["context_hash"] == digest(
        b"original"
    )
