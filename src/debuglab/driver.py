"""Unattended dev, validation, freeze, held-out, and reporting coordinator."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from debuglab.config import Config
from debuglab.io import save
from debuglab.study import execute


def complete(config: Config, bugs: list[str]) -> bool:
    for bug in bugs:
        path = Path(config.output_root) / bug / "result.json"
        if not path.exists():
            return False
        data = json.loads(path.read_text())
        if not data.get("evaluated") or not data.get("pipeline_complete"):
            return False
    return True


def drive(config_path: Path) -> None:
    config = Config.load(config_path)
    root = Path(config.output_root).parent

    def state(stage: str, **values: object) -> None:
        save(root / "driver-state.json", {"stage": stage, "updated_unix": time.time(), **values})
        print(f"[driver] {stage}", flush=True)

    state("waiting_for_pre_model_screening")
    while not Path(config.split_path).exists():
        time.sleep(30)
    split = json.loads(Path(config.split_path).read_text())
    if len(split["pool"]) < 50:
        raise RuntimeError("Larger experiment requires at least 50 selected bugs")
    for partition in ("dev", "held_out"):
        if partition == "held_out" and not Path(config.freeze_path).exists():
            state(
                "validation_before_remaining"
                if config.allow_pool_tuning
                else "validation_before_held_out"
            )
            pilot = {}
            for bug in split["pilot"]:
                result = json.loads((Path(config.output_root) / bug / "result.json").read_text())
                pilot[bug] = result["context_recall"]
            save(Path(config.pilot_baseline_path), {"bugs": pilot, "real_runs": True})
            with (root / "VERIFICATION.txt").open("w") as log:
                subprocess.run(["scripts/verify"], stdout=log, stderr=subprocess.STDOUT, check=True)
            if config.allow_pool_tuning:
                from debuglab.cli import fingerprint

                save(Path(config.freeze_path), fingerprint(config_path))
            else:
                subprocess.run(
                    ["scripts/debuglab", "freeze", "--config", str(config_path)], check=True
                )
        label = (
            {"dev": "initial_subset", "held_out": "remaining"}[partition]
            if config.allow_pool_tuning
            else partition
        )
        state("running_" + label)
        for retry in range(3):
            if complete(config, split[partition]):
                break
            execute(config_path, partition)
            subprocess.run(
                [sys.executable, "-m", "debuglab_gold.large_report", "--config", str(config_path)],
                check=True,
            )
            if not complete(config, split[partition]):
                state("resume_" + partition, completed_rounds=retry + 1)
        if not complete(config, split[partition]):
            state(
                "blocked", partition=partition, reason="Incomplete bugs after three resume rounds"
            )
            raise RuntimeError("Resolve saved pipeline failures; no bugs were excluded or imputed")
    state("final_reporting")
    subprocess.run(
        [sys.executable, "-m", "debuglab_gold.large_report", "--config", str(config_path)],
        check=True,
    )
    state("complete", evaluated=len(split["pool"]), report=str(root / "REPORT.md"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/local.json"))
    args = parser.parse_args()
    drive(args.config)


if __name__ == "__main__":
    main()
