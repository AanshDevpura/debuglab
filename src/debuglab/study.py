"""Pre-model screening and resumable execution of a separately frozen cohort."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from debuglab.config import Config
from debuglab.harness import Harness, failing_count
from debuglab.io import digest, save


class BugDeadline(BaseException):
    """An overall deadline must escape model/attempt exception handlers."""


def cleanup(config: Config, bug: str) -> None:
    """Remove only this study's disposable checkouts, preserving all artifacts."""
    if not config.cleanup_checkouts:
        return
    root = Path(config.work_root).resolve()
    if not root.is_relative_to(Path("work").resolve()):
        raise ValueError("Cleanup requires a checkout root inside workspace work/")
    attempts = root.parent / "attempts" / bug
    if attempts.exists():
        for logs in attempts.glob("*-logs"):
            archive = Path(config.output_root) / bug / "evaluation-logs" / logs.name
            suffix = 0
            while archive.exists():
                old = {
                    p.relative_to(archive).as_posix(): digest(p.read_bytes())
                    for p in archive.rglob("*")
                    if p.is_file()
                }
                new = {
                    p.relative_to(logs).as_posix(): digest(p.read_bytes())
                    for p in logs.rglob("*")
                    if p.is_file()
                }
                if old == new:
                    break
                suffix += 1
                archive = archive.with_name(logs.name + f"-cleanup-{suffix}")
            else:
                shutil.copytree(logs, archive)
    paths = [root / bug, attempts, Path("work/gold") / bug / "fixed"]
    for path in paths:
        if path.exists():
            shutil.rmtree(path)


def screen(config: Config, plan: Path) -> None:
    selection = json.loads(plan.read_text())
    root = Path(config.output_root).parent
    active = {}
    for project in ("Lang", "Math", "Chart", "Time"):
        result = subprocess.run(
            [str(Path(config.defects4j).resolve()), "bids", "-p", project],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        active[project] = sorted({int(n) for n in re.findall(r"\d+", result.stdout)})
    save(root / "active-bug-ids.json", active)
    candidates = [
        bug
        for bug in selection["candidates"]
        if int(bug.split("-")[1]) in active[bug.split("-")[0]]
    ]
    selection["inactive_metadata_ids"] = sorted(
        set(selection.get("inactive_metadata_ids", []))
        | (set(selection["candidates"]) - set(candidates))
    )
    selection["candidates"] = candidates
    save(plan, selection)
    screened = []
    settings = replace(config, work_root="work/screen", command_timeout=120)
    for i, bug in enumerate(candidates, 1):
        folder = root / "screening" / bug
        record_path = folder / "screen.json"
        if record_path.exists():
            screened.append(json.loads(record_path.read_text()))
            continue
        harness = Harness(settings, bug, folder / "commands")
        if harness.root.exists():
            shutil.rmtree(harness.root)
        record = {"bug": bug, "eligible": False, "model_calls": 0, "suite_runs": []}
        try:
            harness.checkout()
            compiled = harness.compile()
            if compiled.timed_out:
                record["reason"] = "baseline_compile_timeout"
            elif compiled.returncode:
                record["reason"] = "baseline_does_not_compile"
            else:
                expected = sorted(harness.export("tests.trigger").splitlines())
                record["expected_triggers"] = expected
                for repetition in range(2):
                    native = harness.test()
                    failure_file = harness.root / "failing_tests"
                    failures = failure_file.read_text() if failure_file.exists() else ""
                    (folder / f"suite-{repetition}-failures.txt").write_text(failures)
                    observed = sorted(re.findall(r"(?m)^--- (.+)$", failures))
                    record["suite_runs"].append(
                        {
                            "seconds": native.seconds,
                            "timed_out": native.timed_out,
                            "returncode": native.returncode,
                            "failures": observed,
                        }
                    )
                    if native.timed_out:
                        record["reason"] = "baseline_suite_exceeds_120_seconds"
                        break
                    if (
                        native.returncode
                        or observed != expected
                        or failing_count(native) != len(expected)
                    ):
                        record["reason"] = "baseline_failure_set_unstable_or_nonreproducing"
                        break
                else:
                    record.update(eligible=True, reason=None)
        except Exception as error:
            record.update(reason="pre_model_environment_failure", error=str(error))
        finally:
            save(record_path, record)
            if harness.root.exists():
                shutil.rmtree(harness.root)
        screened.append(record)
        print(f"[screen {i}/{len(candidates)}] {bug}: {record['reason'] or 'eligible'}", flush=True)
    environment = [r for r in screened if r["reason"] == "pre_model_environment_failure"]
    if environment:
        raise RuntimeError(
            "Resolve screening environment failures before selecting or running models"
        )
    eligible = [r["bug"] for r in screened if r["eligible"]]
    target = selection["target"]
    if len(eligible) < target:
        raise RuntimeError(
            f"Only {len(eligible)} eligible bugs; expand the pre-model candidate pool"
        )
    chosen = eligible[:target]
    random.Random(selection["seed"] + 1).shuffle(chosen)
    dev = chosen[: selection["dev_count"]]
    save(
        Path(config.split_path),
        {
            "seed": selection["seed"],
            "pool": chosen,
            "dev": dev,
            "held_out": chosen[len(dev) :],
            "pilot": dev[:5],
        },
    )
    save(root / "exclusions.json", [r for r in screened if not r["eligible"]])
    save(
        root / "selection.json",
        {
            "selected": chosen,
            "eligible_reserve": eligible[target:],
            "selection_before_cohort_model_calls": True,
        },
    )


def execute(config_path: Path, partition: str) -> None:
    config = Config.load(config_path)
    split = json.loads(Path(config.split_path).read_text())
    root = Path(config.output_root).parent
    for bug in split[partition]:
        result_file = Path(config.output_root) / bug / "result.json"
        if result_file.exists():
            result = json.loads(result_file.read_text())
            if result.get("evaluated") and result.get("pipeline_complete"):
                cleanup(config, bug)
                continue
        log = root / "worker-logs" / f"{bug}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        artifacts = Path(config.output_root) / bug
        checkout = Path(config.work_root).resolve() / bug
        if checkout.exists() and not (artifacts / "metadata.json").exists():
            shutil.rmtree(checkout)
        # Single-bug CLI retains its dev/held-out authorization and frozen-code guards.
        with log.open("a") as handle:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "debuglab.study",
                    "one",
                    "--config",
                    str(config_path),
                    "--partition",
                    partition,
                    "--bug",
                    bug,
                ],
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            started = time.monotonic()
            timed_out = False
            try:
                process.wait(timeout=config.bug_timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        timed_out = timed_out or (artifacts / "budget-timeout.json").exists()
        save(
            root / "worker-status" / f"{bug}.json",
            {
                "bug": bug,
                "partition": partition,
                "seconds": time.monotonic() - started,
                "returncode": process.returncode,
                "timed_out": timed_out,
            },
        )
        if process.returncode:
            print(f"[{bug}] needs resume; see {log}", flush=True)
        else:
            print(f"[{bug}] evaluated and cleaned", flush=True)
        cleanup(config, bug)


def one(config_path: Path, partition: str, bug: str) -> None:
    from debuglab.cli import fingerprint, sealed
    from debuglab.model import TokenBudget
    from debuglab.pipeline import run_bug

    config = Config.load(config_path)
    split = json.loads(Path(config.split_path).read_text())
    if bug not in split[partition]:
        raise ValueError("Bug does not belong to requested partition")
    run_freeze = Path(config.output_root).parent / "run-fingerprint.json"
    if run_freeze.exists() and json.loads(run_freeze.read_text()) != fingerprint(config_path):
        raise RuntimeError("Run code/config/split changed before worker start")
    if partition == "held_out" and not config.allow_pool_tuning:
        frozen = json.loads(Path(config.freeze_path).read_text())
        if fingerprint(config_path) != frozen:
            raise RuntimeError("Held-out code/config/split changed")
        marker = Path(config.output_root).parent / "held-out-started.json"
        if marker.exists() and json.loads(marker.read_text()) != frozen:
            raise RuntimeError("Cannot resume a different held-out experiment")
        save(marker, frozen)
    deadline = time.monotonic() + config.bug_timeout - 5
    deadline_key = "DEBUGLAB_BUG_DEADLINE_MONOTONIC"
    previous_deadline = os.environ.get(deadline_key)
    os.environ[deadline_key] = str(deadline)

    def expire(signum: int, frame: object) -> None:
        for path in (Path(config.output_root) / bug / "calls").glob("*.json"):
            record = json.loads(path.read_text())
            if record.get("status") == "IN_FLIGHT":
                record.update(status="TIMEOUT", error="Overall bug budget expired; never resampled")
                save(path, record)
        save(
            Path(config.output_root) / bug / "budget-timeout.json",
            {
                "bug": bug,
                "deadline": deadline,
                "deadline_clock": "monotonic",
                "unattempted_conditions_not_imputed": True,
            },
        )
        raise BugDeadline(f"Overall bug budget expired: {bug}")

    signal.signal(signal.SIGALRM, expire)
    signal.alarm(config.bug_timeout - 5)
    try:
        output = run_bug(config, bug, TokenBudget(config.model))
        sealed(config_path, output)
    finally:
        signal.alarm(0)
        if previous_deadline is None:
            os.environ.pop(deadline_key, None)
        else:
            os.environ[deadline_key] = previous_deadline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["screen", "execute", "one"])
    parser.add_argument("--config", type=Path, default=Path("configs/local.json"))
    parser.add_argument("--plan", type=Path, default=Path("outputs/final/candidates.json"))
    parser.add_argument("--partition", choices=["dev", "held_out"], default="dev")
    parser.add_argument("--bug")
    args = parser.parse_args()
    if args.command == "screen":
        screen(Config.load(args.config), args.plan)
    elif args.command == "execute":
        execute(args.config, args.partition)
    else:
        if not args.bug:
            raise ValueError("A bug id is required")
        one(args.config, args.partition, args.bug)


if __name__ == "__main__":
    main()
