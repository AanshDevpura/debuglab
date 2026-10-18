"""Authorized comparisons using existing immutable debugging inputs."""

from __future__ import annotations

import shutil
from pathlib import Path

from debuglab.config import Config
from debuglab.io import digest, save
from debuglab.selection import read_bugs

ARTIFACTS = (
    "metadata.json",
    "methods.json",
    "skeletons.json",
    "failure.json",
    "spectrum.json",
    "executed-methods.json",
    "context.txt",
    "context-manifest.json",
)


def reuse(config: Config, bug: str, source_root: Path, *, rebuild_context: bool = False) -> None:
    allowed = read_bugs(Path(config.bugs_path))
    if bug not in allowed:
        raise ValueError("Bug is not in the selected evaluation set")
    source = source_root.resolve() / bug
    output = Path(config.output_root).resolve() / bug
    old_config = Config.load(source / "config.json")
    original = Path(old_config.work_root).resolve() / bug
    target = Path(config.work_root).resolve() / bug
    if output == source or target == original:
        raise ValueError("Replay requires separate result and checkout roots")
    output.mkdir(parents=True, exist_ok=True)
    if original.exists() and not target.exists():
        shutil.copytree(original, target)
    hashes = {}
    artifacts = (
        ARTIFACTS
        + (("source-hashes.json",) if (source / "source-hashes.json").exists() else ())
        + (("source-units.json",) if (source / "source-units.json").exists() else ())
    )
    if rebuild_context:
        artifacts = tuple(
            name
            for name in artifacts
            if name not in {"context.txt", "context-manifest.json", "source-units.json"}
        )
    for name in artifacts:
        data = (source / name).read_bytes()
        path = output / name
        if path.exists() and path.read_bytes() != data:
            raise RuntimeError(f"Replay inputs changed: {name}")
        if not path.exists():
            path.write_bytes(data)
        hashes[name] = digest(data)
    save(
        output / "replay-provenance.json",
        {
            "source": str(source),
            "hashes": hashes,
            "selection": "all_selected_bugs",
            "ground_truth_inputs": False,
            "frozen_context_reused": not rebuild_context,
        },
    )
