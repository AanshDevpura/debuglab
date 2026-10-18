"""Failure attribution and input audits for the real, larger 7B cohort."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import TextIO

from debuglab.config import Config
from debuglab.io import digest, save
from debuglab.selection import read_bugs
from debuglab.source import Method
from debuglab_gold.report import report, write_csv


def analyze(config: Config) -> None:
    report(config)
    root = Path(config.output_root).parent
    selected_bugs = read_bugs(Path(config.bugs_path))
    rows, localization_rows, audits, missing = [], [], [], []
    for bug in selected_bugs:
        artifacts = Path(config.output_root) / bug
        path = artifacts / "result.json"
        if not path.exists():
            missing.append(bug)
            continue
        result = json.loads(path.read_text())
        if not result.get("evaluated") or not result.get("pipeline_complete"):
            missing.append(bug)
            continue
        gold = json.loads((Path("work/gold") / bug / "locations.json").read_text())
        methods = [Method(**m) for m in json.loads((artifacts / "methods.json").read_text())]
        targets = [m for m in methods if m.id in gold["methods"]]
        context = (artifacts / "context.txt").read_bytes()
        assert digest(context) == result["context_hash"]
        assert not result["leak_violations"]
        for ranker, ranking in result["localization"].items():
            method_metric = ranking["metrics"]["method"]
            localization_rows.append(
                {
                    "bug": bug,
                    "ranker": ranker,
                    "parse_error": ranking.get("parse_error"),
                    "method_rank": method_metric["rank"],
                    "line_rank": ranking["metrics"]["line"]["rank"],
                    "faulty_method_in_context": result["context_recall"]["method_any"],
                    "method_target_mapped": bool(targets),
                    "outcome": (
                        "invalid_output"
                        if ranking.get("parse_error")
                        else "unmapped_fault_location"
                        if not targets
                        else "not_in_context"
                        if ranker != "spectrum" and not result["context_recall"]["method_any"]
                        else "fault_not_ranked"
                        if method_metric["rank"] is None
                        else "ranked_below_top1"
                        if not method_metric["top1"]
                        else "faulty_method_top1"
                    ),
                }
            )
        for call_path in (artifacts / "calls").glob("*.json"):
            call = json.loads(call_path.read_text())
            prompt = call["parameters"]["messages"][1]["content"]
            assert prompt.startswith(context.decode() + "\n\nLOCALIZATION HINT:\n")
            assert call.get("input_tokens", call.get("input_tokens_exact")) <= (
                call["context_window"]
                - call["parameters"].get(
                    "max_tokens",
                    call["parameters"].get("max_completion_tokens", config.model.max_output_tokens),
                )
                - config.model.safety_margin
            )
            audits.append(
                {
                    "bug": bug,
                    "call": call_path.name,
                    "frozen_context_in_prompt": True,
                    "input_tokens": call.get("input_tokens", call.get("input_tokens_exact")),
                    "faulty_method_in_actual_prompt": any(
                        "METHOD " + m.id + "\n" + m.numbered() in prompt for m in targets
                    ),
                    "token_count_method": call.get("token_count_method", "local_chat_template"),
                    "provider_input_tokens": call.get("usage", {}).get("prompt_tokens"),
                    "finish_reason": call.get("finish_reason"),
                    "status": call["status"],
                }
            )
        for condition, attempts in result["repair"].items():
            for sample, attempt in enumerate(attempts):
                overlaps = any(
                    edit["file"] == method.file
                    and edit["start_line"] <= method.end
                    and edit["end_line"] >= method.start
                    for edit in attempt["edits"]
                    for method in targets
                )
                status = attempt["status"]
                category = {
                    "INVALID_FORMAT": "invalid_output",
                    "INVALID_APPLY": "invalid_output",
                    "SYNTAX_ERROR": "invalid_output",
                    "COMPILE_ERROR": "compile_error",
                    "MODEL_ERROR": "model_error",
                    "TIMEOUT": "timeout",
                    "PLAUSIBLE": "plausible",
                }.get(
                    status,
                    "unmapped_fault_location"
                    if not targets
                    else "wrong_fix"
                    if overlaps
                    else "wrong_location",
                )
                rows.append(
                    {
                        "bug": bug,
                        "condition": condition,
                        "sample": sample,
                        "status": status,
                        "attempt_category": category,
                        "overlaps_faulty_method": overlaps,
                        "faulty_method_in_context": result["context_recall"]["method_any"],
                        "method_target_mapped": bool(targets),
                    }
                )
    save(root / "prompt-audit.json", audits)
    save(
        root / "completion.json",
        {
            "expected": len(selected_bugs),
            "missing": missing,
            "evaluated": len(selected_bugs) - len(missing),
            "complete": not missing,
        },
    )
    if rows:
        write_csv(root / "failure-analysis.csv", rows)
    if localization_rows:
        write_csv(root / "localization-failure-analysis.csv", localization_rows)
    save(
        root / "localization-failure-summary.json",
        {
            ranker: {
                "bugs": len(selected),
                "outcomes": dict(Counter(row["outcome"] for row in selected)),
                "parse_errors": dict(
                    Counter(row["parse_error"] for row in selected if row["parse_error"])
                ),
            }
            for ranker in ("spectrum", "llm", "combined")
            for selected in [[row for row in localization_rows if row["ranker"] == ranker]]
        },
    )
    summary = {
        "attempts": len(rows),
        "attempt_causes": dict(Counter(r["attempt_category"] for r in rows)),
        "attempts_with_faulty_method_missing": sum(not r["faulty_method_in_context"] for r in rows),
        "attempts_with_unmapped_method_target": sum(not r["method_target_mapped"] for r in rows),
    }
    save(root / "failure-summary.json", summary)
    with (root / "REPORT.md").open("a") as handle:
        handle.write("\n## Saved-call audits and failure analysis\n\n")
        handle.write(
            "The selected bug list is frozen before calls. Every failed model or repair "
            "outcome remains counted. Saved prompts retain frozen context, budget checks "
            "and sealed leak audits. Counts are local-template counts or labeled estimates; "
            "provider usage is recorded separately. Logs survive cleanup and completed "
            "calls are not resampled.\n\n"
            "Failure categories are proxies: wrong location means a compiling failed patch "
            "does not overlap a known faulty method, while wrong fix means it does. "
            "Retrieval misses and unmapped faults are flags, not exclusions.\n\n"
            f"All evaluated bugs: {summary['attempts']} actual attempts; "
            f"{json.dumps(summary['attempt_causes'], sort_keys=True)}.\n"
        )
        if missing:
            handle.write(
                "\nThis report is partial. Missing bugs remain selected and are not "
                "imputed: " + ", ".join(missing) + ".\n"
            )
        hypothesis(config, selected_bugs, handle)


def hypothesis(config: Config, ids: list[str], handle: TextIO) -> None:
    held = []
    label = "selected-set"
    for bug in ids:
        path = Path(config.output_root) / bug / "result.json"
        if path.exists():
            result = json.loads(path.read_text())
            if result.get("evaluated") and result.get("pipeline_complete"):
                held.append(result)
    if not held or len(held) != len(ids):
        return
    handle.write(f"\n## What the {label} results say about the hypothesis\n\n")
    for level in ("method", "line"):
        eligible = [b for b in held if b["gold_eligibility"][level]]
        rates = (
            {
                ranker: sum(b["localization"][ranker]["metrics"][level]["top1"] for b in eligible)
                / len(eligible)
                for ranker in ("spectrum", "llm", "combined")
            }
            if eligible
            else {}
        )
        handle.write(
            f"{level.capitalize()} Top-1 on {len(eligible)} eligible {label} bugs: "
            f"{json.dumps(rates, sort_keys=True)}. "
        )
        if rates:
            improves = rates["combined"] > max(rates["spectrum"], rates["llm"])
            handle.write(
                "Combined "
                + ("does" if improves else "does not")
                + " exceed both baselines on this metric.\n\n"
            )
    success = {
        condition: sum(
            any(a["status"] == "PLAUSIBLE" for a in b["repair"][condition]) for b in held
        )
        / len(held)
        for condition in ("direct", "spectrum", "llm", "combined")
    }
    handle.write(
        f"{label.capitalize()} repair pass@1: "
        + json.dumps(success, sort_keys=True)
        + ". These point estimates describe this configured model and selection, not every LLM. "
        "The same frozen context/failure data across conditions isolates "
        "localization hints. Sparse successes, invalid output, retrieval "
        "misses and method-interface limitations restrict interpretation; "
        "conditional tables show whether missing context explains failures.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/local.json"))
    args = parser.parse_args()
    analyze(Config.load(args.config))


if __name__ == "__main__":
    main()
