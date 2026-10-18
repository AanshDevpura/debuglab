"""CLI coordinator; sealed evaluation is invoked only through a subprocess boundary."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from debuglab.config import Config
from debuglab.io import digest, save
from debuglab.model import TokenBudget
from debuglab.pipeline import run_bug
from debuglab.replay import reuse


def sealed(config_path: Path, output: Path) -> None:
    result = output / "result.json"
    if result.exists() and json.loads(result.read_text()).get("evaluated"):
        return
    subprocess.run(
        [
            sys.executable,
            "-m",
            "debuglab_gold.cli",
            "evaluate",
            "--config",
            str(config_path.resolve()),
            "--artifacts",
            str(output),
        ],
        check=True,
    )


def fingerprint(config: Path) -> dict:
    settings = Config.load(config)
    sources = sorted(Path("src").rglob("*.py")) + sorted(Path("java").rglob("*.java"))
    return {
        "config": digest(config.read_bytes()),
        "split": digest(Path(settings.split_path).read_bytes()),
        "code": digest(b"".join(path.as_posix().encode() + path.read_bytes() for path in sources)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Real Defects4J localization/repair experiments")
    parser.add_argument("command", choices=["run", "batch", "freeze", "report"])
    parser.add_argument("--bug")
    parser.add_argument("--config", type=Path, default=Path("configs/local.json"))
    parser.add_argument("--partition", choices=["pilot", "dev", "held_out"], default="pilot")
    parser.add_argument("--reuse-from", type=Path, help="Reuse frozen debugging inputs (dev only)")
    args = parser.parse_args()
    config = Config.load(args.config)
    if args.command == "report":
        subprocess.run(
            [
                sys.executable,
                "-m",
                "debuglab_gold.cli",
                "report",
                "--config",
                str(args.config.resolve()),
            ],
            check=True,
        )
        return
    if args.command == "freeze":
        baseline = Path(config.pilot_baseline_path)
        if not baseline.exists():
            raise RuntimeError("A real pilot recall baseline is required before freezing")
        save(Path(config.freeze_path), fingerprint(args.config))
        print("Config, split and code frozen for held-out evaluation", flush=True)
        return
    split = json.loads(Path(config.split_path).read_text())
    if args.command == "run":
        if not args.bug or args.bug not in split["dev"]:
            raise ValueError("Single-bug runs are dev only; use frozen held-out batch")
        bugs = [args.bug]
    else:
        bugs = split[args.partition]
    if args.command == "batch" and args.partition == "held_out":
        frozen = json.loads(Path(config.freeze_path).read_text())
        if frozen != fingerprint(args.config):
            raise RuntimeError("Code/config/split differs from the frozen experiment")
        marker = Path(config.output_root) / "held-out-started.json"
        if marker.exists():
            started = json.loads(marker.read_text())
            if any(started[field] != frozen[field] for field in ("config", "split")):
                raise RuntimeError("Cannot resume held-out with a changed config or split")
            if all(
                (Path(config.output_root) / bug / "result.json").exists()
                and json.loads((Path(config.output_root) / bug / "result.json").read_text()).get(
                    "pipeline_complete"
                )
                for bug in bugs
            ):
                raise RuntimeError("Held-out already complete; model attempts cannot be rerun")
            save(Path(config.output_root) / "held-out-resume.json", frozen)
        else:
            save(marker, frozen)
    if args.reuse_from and args.partition == "held_out":
        raise ValueError("Held-out replay is forbidden")
    budget = TokenBudget(config.model)
    for bug in bugs:
        try:
            if args.reuse_from:
                reuse(config, bug, args.reuse_from)
            output = run_bug(config, bug, budget)
            sealed(args.config, output)
        except Exception as error:
            save(
                Path(config.output_root) / bug / "blocked.json",
                {"bug": bug, "error_type": type(error).__name__, "error": str(error)},
            )
            raise
    subprocess.run(
        [
            sys.executable,
            "-m",
            "debuglab_gold.cli",
            "report",
            "--config",
            str(args.config.resolve()),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
