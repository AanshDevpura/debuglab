"""Export final-report data from two sealed runs; never makes model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from debuglab.model import openai_schema

CONDITIONS = ("direct", "spectrum", "llm", "combined")


def read(path: Path):
    return json.loads(path.read_text())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(qwen: Path, gpt: Path, review: Path, output: Path) -> None:
    if output.exists():
        raise ValueError("Choose a new output directory; published evidence is immutable")
    bugs = read(qwen / "bugs.json")
    if bugs != read(gpt / "bugs.json") or len(bugs) != 100 or len(set(bugs)) != 100:
        raise ValueError("Expected the same 100 bugs in the same order")
    roots = {"qwen": qwen, "gpt": gpt}
    reviews = read(review)["groups"]
    index = {(g["model"], g["bug"], c): g for g in reviews for c in g["conditions"]}
    data = {
        "bugs": bugs,
        "models": {},
        "localization": [],
        "attempts": [],
        "inputs": [],
        "requests": [],
        "review_source_sha256": sha(review),
    }
    for model, root in roots.items():
        config = read(root / "config.json")
        data["models"][model] = {
            "model": config["model"],
            "source_archive_sha256": sha(root / "source-at-start.zip"),
            "selected": 100,
            "calls": 0,
            "attempts": 0,
        }
        for bug in bugs:
            folder = root / "runs" / bug
            result = read(folder / "result.json")
            if not result.get("evaluated") or not result.get("pipeline_complete"):
                raise ValueError(f"Incomplete {model}/{bug}")
            data["inputs"].append(
                {
                    "model": model,
                    "bug": bug,
                    "context_sha256": result["context_hash"],
                    "failure_sha256": result["failure_hash"],
                    "all_faulty_methods_in_context": result["context_recall"]["method_all"],
                }
            )
            for ranker in ("spectrum", "llm", "combined"):
                ranking = result["localization"][ranker]
                for level in ("method", "line"):
                    data["localization"].append(
                        {
                            "model": model,
                            "bug": bug,
                            "ranker": ranker,
                            "level": level,
                            **ranking["metrics"][level],
                            "parse_error": ranking.get("parse_error"),
                        }
                    )
            for condition in CONDITIONS:
                attempts = result["repair"][condition]
                if len(attempts) != 1:
                    raise ValueError("Exactly one answer per condition required")
                attempt = attempts[0]
                row = {
                    "model": model,
                    "bug": bug,
                    "condition": condition,
                    "status": attempt["status"],
                    "review": None,
                    "review_id": None,
                }
                if attempt["status"] == "PLAUSIBLE":
                    g = index[model, bug, condition]
                    row.update(review=g["assessment"], review_id=f"{model}-{bug}-{g['group']}")
                    proofs = []
                    for path in sorted((folder / "evaluation-logs").rglob("*test.json")):
                        if not path.parent.name.startswith(condition + "-0"):
                            continue
                        log = read(path)
                        if log["command"][-1] == "test" and "-t" not in log["command"]:
                            counts = re.findall(r"Failing tests:\s*(\d+)", log["output"])
                            if (
                                log["returncode"] == 0
                                and not log["timed_out"]
                                and counts
                                and counts[-1] == "0"
                            ):
                                proofs.append(
                                    {
                                        "log_sha256": sha(path),
                                        "command": ["defects4j", "test"],
                                        "returncode": 0,
                                        "timed_out": False,
                                        "last_failing_tests": 0,
                                    }
                                )
                    if not proofs:
                        raise ValueError(f"Missing full-suite proof {model}/{bug}/{condition}")
                    row["native_proofs"] = proofs
                row["edits_sha256"] = hashlib.sha256(
                    json.dumps(attempt["edits"], sort_keys=True).encode()
                ).hexdigest()
                data["attempts"].append(row)
                data["models"][model]["attempts"] += 1
            calls = list((folder / "calls").glob("*.json"))
            if len(calls) != 6:
                raise ValueError("Expected exactly six calls per bug")
            data["models"][model]["calls"] += len(calls)
    for bug in bugs:
        old, new = qwen / "runs" / bug, gpt / "runs" / bug
        for name in (
            "context.txt",
            "failure.json",
            "methods.json",
            "source-hashes.json",
            "spectrum.json",
        ):
            if (old / name).read_bytes() != (new / name).read_bytes():
                raise ValueError(f"Frozen inputs differ: {bug}/{name}")
        for path in sorted((new / "calls").glob("*.json")):
            a, b = read(old / "calls" / path.name), read(path)
            fixed = path.name in {
                "localize-llm.json",
                "localize-combined.json",
                "repair-direct-0.json",
                "repair-spectrum-0.json",
            }
            expected = json.loads(json.dumps(a["parameters"]["response_format"]))
            expected["json_schema"]["schema"] = openai_schema(expected["json_schema"]["schema"])
            if expected != b["parameters"]["response_format"]:
                raise ValueError("Schema differs after provider encoding")
            equal = a["parameters"]["messages"] == b["parameters"]["messages"]
            if fixed and not equal:
                raise ValueError("Fixed-input messages differ")
            data["requests"].append(
                {
                    "bug": bug,
                    "call": path.name,
                    "fixed_input": fixed,
                    "messages_equal": equal,
                    "normalized_schema_equal": True,
                    "qwen_call_sha256": sha(old / "calls" / path.name),
                    "gpt_call_sha256": sha(path),
                }
            )
    output.mkdir(parents=True)
    (output / "data.json").write_text(json.dumps(data, indent=2) + "\n")
    folder = output / "patches"
    folder.mkdir()
    for g in reviews:
        row = {
            k: g[k]
            for k in [
                "model",
                "bug",
                "group",
                "conditions",
                "assessment",
                "assessment_basis",
                "methods",
                "unmodified_reference_changed_methods",
            ]
        }
        # Review paths are local artifacts; relative, self-contained records replace them.
        (folder / f"{g['model']}-{g['bug']}-{g['group']}.json").write_text(
            json.dumps(row, indent=2) + "\n"
        )
    manifest = {
        str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("qwen", "gpt", "review", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    a = parser.parse_args()
    export(a.qwen, a.gpt, a.review, a.output)
    print(f"Exported {a.output}; source artifacts unchanged")


if __name__ == "__main__":
    main()
