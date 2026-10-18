import zipfile
from pathlib import Path

from debuglab.artifacts import archive


def test_archive_excludes_runtime_and_build_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "src/debuglab/run.py",
        "src/debuglab/__pycache__/run.pyc",
        "src/debuglab.egg-info/PKG-INFO",
        "work/bugs/secret.java",
        "output/private-report.docx",
        "outputs/final/raw-call.json",
        "configs/reproduce.local.json",
        "results/final/SUMMARY.md",
        ".github/workflows/ci.yml",
        "README.md",
    ):
        p = Path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("fixture")
    destination = tmp_path / "release/source.zip"
    archive(destination)
    with zipfile.ZipFile(destination) as zipped:
        assert set(zipped.namelist()) == {
            "src/debuglab/run.py",
            ".github/workflows/ci.yml",
            "README.md",
            "results/final/SUMMARY.md",
        }
        assert zipped.testzip() is None
    assert not destination.with_suffix(".zip.tmp").exists()
