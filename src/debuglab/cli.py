"""Two commands: run the selected bugs, or regenerate their reports."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from debuglab.config import Config
from debuglab.io import digest
from debuglab.selection import read_bugs


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
        "bugs": digest(Path(settings.bugs_path).read_bytes()),
        "code": digest(b"".join(path.as_posix().encode() + path.read_bytes() for path in sources)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="One-shot Defects4J localization and repair")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run all selected bugs, or resume the same run")
    run.add_argument("--config", type=Path, default=Path("configs/local.json"))
    run.add_argument("--bugs", type=Path, help="Override the JSON bug list")
    run.add_argument("--bug", help="Run one bug from the selected list")
    run.add_argument("--output", type=Path, help="Use a separate output directory")
    report = commands.add_parser("report", help="Regenerate reports from saved run evidence")
    report.add_argument("--output", type=Path, default=Path("outputs/local-7b"))
    args = parser.parse_args()
    if args.command == "run":
        from debuglab.cohort_run import coordinate

        coordinate(args.config, bugs_path=args.bugs, bug=args.bug, output=args.output)
    else:
        config_path = args.output / "config.json"
        if not config_path.is_file():
            parser.error("No saved run manifest at this output; run debuglab run first")
        settings = Config.load(config_path)
        read_bugs(Path(settings.bugs_path))
        subprocess.run(
            [sys.executable, "-m", "debuglab_gold.large_report", "--config", str(config_path)],
            check=True,
        )
        print(f"Report: {args.output / 'REPORT.md'}")


if __name__ == "__main__":
    main()
