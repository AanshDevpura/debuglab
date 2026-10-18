"""Atomic, source-only release archives and checked subprocess execution."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path


def archive(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    files = [
        Path("README.md"),
        Path("pyproject.toml"),
        Path(".gitignore"),
        Path("requirements-lock.txt"),
    ]
    for directory in ("src", "tests", "configs", "scripts", "java", ".github", "results/final"):
        files.extend(Path(directory).rglob("*"))
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(files):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and not any(part.endswith(".egg-info") for part in path.parts)
                and path.suffix != ".pyc"
                and not path.name.endswith(".local.json")
            ):
                zipped.write(path)
    temporary.replace(destination)


def command(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True)


def publish_reports(root: Path, destination: Path) -> None:
    """Publish small report artifacts, keeping raw calls/checkouts local."""
    destination.mkdir(parents=True, exist_ok=True)
    for pattern in (
        "*.csv",
        "REPORT.md",
        "comparison.json",
        "final-audit.json",
        "VERIFICATION.txt",
    ):
        for path in root.glob(pattern):
            shutil.copy2(path, destination / path.name)
    (destination / "README.md").write_text(
        "# Final release evaluation\n\n"
        "Fresh 60-bug run after cleanup; one patch per condition, existing local7B. "
        "REPORT.md and CSVs contain pooled and required context-conditional metrics. "
        "comparison.json compares this repeat with the retained reference; "
        "differences are reported without choosing a preferred sample. "
        "Raw prompts, native logs, fingerprints and source archive remain in outputs/final.\n"
    )
