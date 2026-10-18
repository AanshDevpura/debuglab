"""Bug pipeline with immutable debugging context and four repair conditions."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from debuglab.config import Config
from debuglab.edits import Status, evaluate
from debuglab.failure import gather
from debuglab.harness import Harness
from debuglab.interface import MethodInterface
from debuglab.io import digest, save
from debuglab.localization import hint, parse
from debuglab.model import TokenBudget, call, messages
from debuglab.retrieval import build
from debuglab.source import Method, class_units, index
from debuglab.spectrum import collect

PROPERTIES = (
    "dir.src.classes",
    "dir.src.tests",
    "dir.bin.classes",
    "dir.bin.tests",
    "cp.test",
    "tests.trigger",
    "tests.all",
)
CONDITIONS = ("direct", "spectrum", "llm", "combined")


def announce(bug: str, stage: str) -> None:
    print(f"[{bug}] {stage}", flush=True)


def prepare(config: Config, bug: str, output: Path, budget: TokenBudget) -> tuple:
    harness = Harness(config, bug, output / "commands")
    if (output / "metadata.json").exists() and not harness.root.exists():
        announce(bug, "restore disposable buggy checkout for resume")
        harness.checkout()
        compiled = harness.compile()
        if compiled.returncode or compiled.timed_out:
            raise RuntimeError("Restored baseline compile failed: " + compiled.output[-4000:])
    if not (output / "metadata.json").exists():
        announce(bug, "checkout and compile")
        harness.checkout()
        compiled = harness.compile()
        if compiled.returncode or compiled.timed_out:
            raise RuntimeError("Baseline compile failed: " + compiled.output[-4000:])
        meta = {prop: harness.export(prop) for prop in PROPERTIES}
        save(output / "metadata.json", meta)
    meta = json.loads((output / "metadata.json").read_text())
    source_hashes = {
        path.relative_to(harness.root).as_posix(): digest(path.read_bytes())
        for directory in (meta["dir.src.classes"], meta["dir.src.tests"])
        for path in sorted((harness.root / directory).rglob("*.java"))
    }
    hash_file = output / "source-hashes.json"
    if hash_file.exists() and json.loads(hash_file.read_text()) != source_hashes:
        raise RuntimeError("Restored debugging sources differ from the original checkout")
    if not hash_file.exists():
        save(hash_file, source_hashes)
    if not (output / "methods.json").exists():
        methods, skeletons = index(harness.root, meta["dir.src.classes"])
        save(output / "methods.json", [m.record() for m in methods])
        save(output / "skeletons.json", skeletons)
    methods = [Method(**m) for m in json.loads((output / "methods.json").read_text())]
    units = []
    if config.class_unit_edits:
        unit_file = output / "source-units.json"
        if not unit_file.exists():
            save(
                unit_file, [m.record() for m in class_units(harness.root, meta["dir.src.classes"])]
            )
        units = [Method(**m) for m in json.loads(unit_file.read_text())]
    skeletons = json.loads((output / "skeletons.json").read_text())
    if not (output / "failure.json").exists():
        announce(bug, "reproduce triggering tests and freeze failure info")
        gather(harness, meta, output)
    failure = json.loads((output / "failure.json").read_text())
    failure["hash"] = digest((output / "failure.json").read_bytes())
    if not (output / "spectrum.json").exists():
        announce(bug, "full-suite per-test spectrum")
        collect(harness, methods, meta, output)
    spectrum = json.loads((output / "spectrum.json").read_text())
    if not (output / "context-manifest.json").exists():
        announce(bug, "retrieve and freeze context")
        build(
            methods,
            skeletons,
            failure,
            frozenset(json.loads((output / "executed-methods.json").read_text())),
            config,
            budget,
            output,
            units,
        )
    manifest = json.loads((output / "context-manifest.json").read_text())
    context = (output / "context.txt").read_bytes()
    if digest(context) != manifest["hash"] or failure["hash"] != manifest["failure_hash"]:
        raise RuntimeError("Frozen debugging artifacts changed")
    return harness, meta, methods, spectrum, manifest, context.decode("utf-8")


def run_bug(config: Config, bug: str, budget: TokenBudget) -> Path:
    output = Path(config.output_root).resolve() / bug
    output.mkdir(parents=True, exist_ok=True)
    config_hash = digest(json.dumps(asdict(config), sort_keys=True).encode())
    config_file = output / "config.json"
    if config_file.exists() and Config.load(config_file) != config:
        raise RuntimeError("Existing bug artifacts belong to another config")
    result_path = output / "result.json"
    if result_path.exists():
        existing = json.loads(result_path.read_text())
        if existing.get("pipeline_complete"):
            announce(bug, "already complete; preserving saved results")
            return output
        result = existing
    else:
        result = {
            "bug": bug,
            "config_hash": config_hash,
            "localization": {},
            "repair": {},
            "pipeline_complete": False,
        }
    save(config_file, asdict(config))
    harness, meta, methods, spectrum, manifest, context = prepare(config, bug, output, budget)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "debuglab_gold.cli",
            "audit",
            "--config",
            str(output / "config.json"),
            "--artifacts",
            str(output),
        ],
        check=True,
    )
    result.update(
        context_hash=manifest["hash"],
        failure_hash=manifest["failure_hash"],
        spectrum_test_count=spectrum["test_count"],
    )
    result["localization"]["spectrum"] = {
        "statement": spectrum["statement"],
        "method": spectrum["method"],
        "parse_error": None,
    }
    calls = output / "calls"
    included = set(manifest["method_ids"])
    interface = (
        MethodInterface(
            context,
            methods,
            config.hint_candidates,
            config.line_array_edits,
            config.java_source_guidance,
            (
                [Method(**m) for m in json.loads((output / "source-units.json").read_text())]
                if config.class_unit_edits
                else []
            ),
            config.executable_line_hints,
        )
        if config.method_interface
        else None
    )
    for condition in ("llm", "combined"):
        if condition in result["localization"]:
            continue
        announce(bug, f"{condition} localization")
        localization_hint = (
            hint(spectrum, config.hint_candidates) if condition == "combined" else None
        )
        prompt = (
            interface.prompt("localize", localization_hint)
            if interface
            else messages(context, "localize", localization_hint)
        )
        response = call(
            config.model,
            budget,
            prompt,
            calls / f"localize-{condition}.json",
            schema=interface.schema("localize") if interface else None,
        )
        ranking_content = response.get("content", "") if response["status"] == "OK" else ""
        ranking = (
            interface.localization(ranking_content)
            if interface
            else parse(ranking_content, methods, included)
        )
        ranking.update(model_status=response["status"])
        result["localization"][condition] = ranking
        save(result_path, result)
    for condition in CONDITIONS:
        attempts = result["repair"].setdefault(condition, [])
        for sample in range(len(attempts), config.samples):
            announce(bug, f"{condition} repair sample {sample + 1}/{config.samples}")
            repair_hint = (
                None
                if condition == "direct"
                else hint(
                    result["localization"][condition],
                    config.hint_candidates,
                )
            )
            if digest((output / "context.txt").read_bytes()) != manifest["hash"]:
                raise RuntimeError("Frozen context changed")
            response = call(
                config.model,
                budget,
                (
                    interface.prompt("repair", repair_hint)
                    if interface
                    else messages(context, "repair", repair_hint)
                ),
                calls / f"repair-{condition}-{sample}.json",
                sample,
                schema=interface.schema("repair") if interface else None,
            )
            if response["status"] != "OK":
                attempt = {
                    "status": Status(response["status"]),
                    "edits": [],
                    "detail": response.get("error", ""),
                }
            else:
                destination = (
                    Path(config.work_root).resolve().parent
                    / "attempts"
                    / bug
                    / (f"{condition}-{sample}")
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    canonical = (
                        interface.edits(response["content"]) if interface else response["content"]
                    )
                except (ValueError, TypeError, KeyError) as error:
                    attempt = {"status": Status.INVALID_FORMAT, "edits": [], "detail": str(error)}
                else:
                    save(
                        output / "mapped-edits" / f"{condition}-{sample}.json",
                        {
                            "raw_call": f"repair-{condition}-{sample}.json",
                            "canonical": json.loads(canonical),
                        },
                    )
                    attempt = evaluate(
                        canonical,
                        harness,
                        meta,
                        destination,
                        log_root=output / "evaluation-logs",
                    )
            attempt.update(
                sample=sample, context_hash=manifest["hash"], failure_hash=manifest["failure_hash"]
            )
            attempts.append(attempt)
            announce(bug, f"{condition}: {attempt['status']}")
            save(result_path, result)
    result["pipeline_complete"] = True
    save(result_path, result)
    return output
