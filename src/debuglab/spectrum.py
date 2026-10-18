"""Full-suite per-test spectra and Ochiai with mean ranks for ties."""

from __future__ import annotations

import math
import shutil
from collections import Counter
from pathlib import Path

from debuglab.harness import Harness, failing_count
from debuglab.io import run, save
from debuglab.source import Method


def ochiai(failed_covered: int, passed_covered: int, total_failed: int) -> float:
    denominator = total_failed * (failed_covered + passed_covered)
    return failed_covered / math.sqrt(denominator) if denominator else 0.0


def ranked(scores: dict[str, float]) -> list[dict]:
    ordered = sorted(scores, key=lambda key: (-scores[key], key))
    result = []
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and scores[ordered[end]] == scores[ordered[start]]:
            end += 1
        rank = ((start + 1) + end) / 2
        result.extend({"id": key, "score": scores[key], "rank": rank} for key in ordered[start:end])
        start = end
    return result


def collect(harness: Harness, methods: list[Method], meta: dict, output: Path) -> dict:
    baseline = harness.test()
    baseline_count = failing_count(baseline)
    if not baseline_count:
        raise RuntimeError("Buggy baseline has no failing tests")
    baseline_file = harness.root / "failing_tests"
    failures = baseline_file.read_text() if baseline_file.exists() else baseline.output
    (output / "baseline-failing-tests.txt").write_text(failures)
    tools = Path("work/tools/jacoco").resolve()
    helper = Path("java/SpectrumRunner.java").resolve()
    helper_classes = tools / "classes"
    helper_classes.mkdir(exist_ok=True)
    junit = (
        Path(harness.config.defects4j).resolve().parents[1]
        / "projects/lib/junit-4.12-hamcrest-1.3.jar"
    )
    cp = (
        meta["cp.test"]
        + ":"
        + str(junit)
        + ":"
        + str(tools / "agent.jar")
        + ":"
        + str(tools / "cli.jar")
    )
    result = run(
        ["javac", "-cp", cp, "-d", str(helper_classes), str(helper)],
        harness.root,
        output / "compile-runner.json",
    )
    if result.returncode:
        raise RuntimeError(result.output)
    tests = output.resolve() / "all-tests.txt"
    tests.write_text(meta["tests.all"] + "\n")
    tsv = output.resolve() / "spectra.tsv"
    compiled_root = harness.root / meta["dir.bin.classes"]
    class_names = sorted(
        {
            path.relative_to(compiled_root).as_posix()[:-6].replace("/", ".")
            for path in compiled_root.rglob("*.class")
        }
    )
    includes = ":".join(class_names)
    java_command = [
        "java",
        "-Xmx4g",
        "-Djava.awt.headless=true",
        "-javaagent:" + str(tools / "agent.jar") + "=output=none,includes=" + includes,
        "-cp",
        str(helper_classes) + ":" + cp,
        "SpectrumRunner",
        str(harness.root / meta["dir.bin.classes"]),
    ]
    legacy = "junit-3." in meta["cp.test"] or "/junit/3." in meta["cp.test"]

    def isolated_coverage() -> None:
        class_runs = output / "class-coverage"
        class_runs.mkdir(exist_ok=True)
        combined, observations = [], []
        for i, name in enumerate(meta["tests.all"].splitlines()):
            class_tests = class_runs.resolve() / f"{i:04d}-tests.txt"
            class_tsv = class_runs.resolve() / f"{i:04d}-spectra.tsv"
            class_tests.write_text(name + "\n")
            command = java_command + [str(class_tests), str(class_tsv)]
            if i:
                command.append("no-universe")
            result = run(
                command,
                harness.root,
                class_runs / f"{i:04d}-run.json",
                harness.config.command_timeout,
            )
            if result.returncode or result.timed_out:
                raise RuntimeError("Class coverage failed: " + result.output[-4000:])
            observations.append(
                {"class": name, "seconds": result.seconds, "returncode": result.returncode}
            )
            combined.append(class_tsv.read_text())
        tsv.write_text("".join(combined))
        save(
            output / "suite-coverage.json",
            {
                "class_isolated": True,
                "classes": observations,
                "seconds": sum(row["seconds"] for row in observations),
            },
        )

    if legacy:
        isolated_coverage()
    else:
        result = run(
            java_command + [str(tests), str(tsv)],
            harness.root,
            output / "suite-coverage.json",
            harness.config.command_timeout,
        )
        if result.returncode or result.timed_out:
            raise RuntimeError("Coverage runner failed: " + result.output[-4000:])
    rows = [line.split("\t") for line in tsv.read_text().splitlines()]
    expected = {line[4:].strip() for line in failures.splitlines() if line.startswith("--- ")}
    observed = {test for test, failure, _ in rows[1:] if failure == "1"}
    if not legacy and (len(observed) != baseline_count or (expected and observed != expected)):
        # Ant reloads test classes; a shared JUnitCore can retain static state between classes.
        # Retry every test class in a fresh JVM, then require the same exact baseline failures.
        recovery = output / "coverage-recovery"
        attempt = 1
        while (recovery / str(attempt)).exists():
            attempt += 1
        archive = recovery / str(attempt)
        archive.mkdir(parents=True)
        shutil.copy2(output / "suite-coverage.json", archive / "suite-coverage.json")
        shutil.copy2(tsv, archive / "spectra.tsv")
        save(
            archive / "reason.json",
            {
                "reason": "shared_jvm_failures_differ_from_native_baseline",
                "native_failures": sorted(expected),
                "shared_jvm_failures": sorted(observed),
                "retry": "all_test_classes_in_fresh_jvms",
            },
        )
        isolated_coverage()
        rows = [line.split("\t") for line in tsv.read_text().splitlines()]
    source_root = meta["dir.src.classes"].rstrip("/")

    def line_ids(cell: str) -> set[str]:
        return {source_root + "/" + item for item in cell.split(",") if item}

    universe = line_ids(rows[0][2])
    passed, failed = Counter(), Counter()
    method_passed, method_failed = Counter(), Counter()
    executed = set()
    failed_tests = set()
    method_by_line = {}
    for method in sorted(methods, key=lambda m: m.end - m.start, reverse=True):
        for line in range(method.start, method.end + 1):
            method_by_line[f"{method.file}:{line}"] = method.id
    for test, failure, cell in rows[1:]:
        lines = line_ids(cell)
        target = failed if failure == "1" else passed
        target.update(lines)
        method_set = set()
        for item in lines:
            method_id = method_by_line.get(item)
            if method_id:
                method_set.add(method_id)
        (method_failed if failure == "1" else method_passed).update(method_set)
        if failure == "1":
            executed.update(method_set)
            failed_tests.add(test)
    if len(failed_tests) != baseline_count or (expected and failed_tests != expected):
        raise RuntimeError(f"Coverage failures differ from Defects4J: {failed_tests} vs {expected}")
    statements = ranked(
        {item: ochiai(failed[item], passed[item], len(failed_tests)) for item in universe}
    )
    method_ranking = ranked(
        {
            method.id: ochiai(method_failed[method.id], method_passed[method.id], len(failed_tests))
            for method in methods
        }
    )
    result_data = {
        "statement": statements,
        "method": method_ranking,
        "test_count": len(rows) - 1,
        "failed_tests": sorted(failed_tests),
        "baseline_failures": baseline_count,
    }
    save(output / "spectrum.json", result_data)
    # Retrieval receives this set only, never ranked coverage or counts.
    save(output / "executed-methods.json", sorted(executed))
    return result_data
