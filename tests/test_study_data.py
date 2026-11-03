"""The published study must remain self-contained and detect evidence corruption."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("study_verify", "scripts/verify-study-data.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_final_study_is_complete_and_keeps_test_passes_separate_from_review():
    result = module.verify(Path("results/study-100"))
    assert result["models"]["qwen"]["suite_passing"] > result["models"]["qwen"]["valid"]
    assert result["models"]["gpt"]["suite_passing"] > result["models"]["gpt"]["valid"]
    assert result["models"]["gpt"]["valid_by_condition"] == [30, 32, 31, 31]


def test_verifier_rejects_changed_evidence(tmp_path):
    import shutil

    shutil.copytree("results/study-100/evidence", tmp_path / "evidence")
    path = tmp_path / "evidence/data.json"
    path.write_text(path.read_text().replace('"selected": 100', '"selected": 99', 1))
    with pytest.raises(ValueError, match="Changed evidence"):
        module.verify(tmp_path)
