"""Pooled localization and repair metrics from one selected evaluation set."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from statistics import mean

from debuglab.config import Config
from debuglab.edits import Status
from debuglab.io import save
from debuglab.selection import read_bugs

RANKERS = ("spectrum", "llm", "combined")
CONDITIONS = ("direct", "spectrum", "llm", "combined")
SUBSETS = ("overall", "faulty_method_in_context", "all_faulty_methods_in_context")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def report(config: Config) -> None:
    output = Path(config.output_root).parent
    output.mkdir(parents=True, exist_ok=True)
    selected = read_bugs(Path(config.bugs_path))
    evaluated, missing = [], []
    for bug in selected:
        path = Path(config.output_root) / bug / "result.json"
        result = json.loads(path.read_text()) if path.exists() else {}
        if result.get("evaluated") and result.get("pipeline_complete"):
            evaluated.append(result)
        else:
            missing.append(bug)
    save(
        output / "completion.json",
        {
            "expected": len(selected),
            "evaluated": len(evaluated),
            "missing": missing,
            "complete": not missing,
        },
    )
    localizations, repairs, per_bug = [], [], []
    for subset in SUBSETS:
        bugs = [
            b
            for b in evaluated
            if subset == "overall"
            or b["context_recall"][
                "method_all" if subset == "all_faulty_methods_in_context" else "method_any"
            ]
        ]
        for ranker in RANKERS:
            for level in ("method", "line"):
                metrics = [
                    b["localization"][ranker]["metrics"][level]
                    for b in bugs
                    if b["localization"][ranker]["metrics"][level]["eligible"]
                ]
                ranks = [m["rank"] for m in metrics if m["rank"] is not None]
                misses = len(metrics) - len(ranks)
                localizations.append(
                    {
                        "subset": subset,
                        "ranker": ranker,
                        "level": level,
                        "eligible_bugs": len(metrics),
                        "unranked": misses,
                        "parse_failures": sum(
                            bool(b["localization"][ranker].get("parse_error")) for b in bugs
                        ),
                        "mean_rank": "INF" if misses else mean(ranks) if ranks else "",
                        "mean_rank_when_ranked": mean(ranks) if ranks else "",
                        **{
                            f"top{k}": mean(m[f"top{k}"] for m in metrics) if metrics else ""
                            for k in (1, 3, 5)
                        },
                    }
                )
        for condition in CONDITIONS:
            attempts = [a for b in bugs for a in b["repair"][condition]]
            if len(attempts) != len(bugs):
                raise ValueError("Completed bugs must have exactly one attempt per condition")
            statuses = Counter(a["status"] for a in attempts)
            successes = statuses["PLAUSIBLE"]
            repairs.append(
                {
                    "subset": subset,
                    "condition": condition,
                    "bugs": len(bugs),
                    "attempts": len(attempts),
                    "plausible_bugs": successes,
                    "pass_at_configured_k": successes / len(bugs) if bugs else "",
                    "applied_edit_rate": mean(
                        a["status"]
                        not in {"INVALID_FORMAT", "INVALID_APPLY", "MODEL_ERROR", "TIMEOUT"}
                        for a in attempts
                    )
                    if attempts
                    else "",
                    "valid_edit_rate": mean(
                        a["status"]
                        in {"COMPILE_ERROR", "FAILS_TRIGGER", "FAILS_SUITE", "PLAUSIBLE"}
                        for a in attempts
                    )
                    if attempts
                    else "",
                    **{status.value: statuses[status.value] for status in Status},
                }
            )
    eligible = [b for b in evaluated if b["context_recall"]["line_eligible"]]
    recall_rows = [
        {
            "bugs": len(evaluated),
            "expected": len(selected),
            **{
                field: mean(b["context_recall"][field] for b in evaluated) if evaluated else ""
                for field in ("file_any", "file_all", "method_any", "method_all")
            },
            "line_eligible": len(eligible),
            **{
                field: mean(b["context_recall"][field] for b in eligible) if eligible else ""
                for field in ("line_any", "line_all", "line_fraction")
            },
        }
    ]
    for b in evaluated:
        per_bug.append(
            {
                "bug": b["bug"],
                "context_hash": b["context_hash"],
                "method_target_mapped": b["gold_eligibility"].get("method_target_mapped", True),
                **{
                    k: v
                    for k, v in b["context_recall"].items()
                    if k not in {"missing", "missing_lines"}
                },
                "leak_violations": len(b["leak_violations"]),
            }
        )
    for name, rows in [
        ("localization", localizations),
        ("repair", repairs),
        ("context-recall", recall_rows),
        ("per-bug", per_bug),
    ]:
        write_csv(output / f"{name}.csv", rows)
    lines = [
        "# Defects4J evaluation",
        "",
        f"Evaluated {len(evaluated)} / {len(selected)} selected bugs.",
        "",
        "One selected evaluation set; no training/development/test partitions.",
        "Missing or incomplete runs remain selected and are listed below, never silently excluded.",
        "Partial-table rates describe completed bugs only; they are not full-set results.",
        "All generated failures remain counted. No missing outcomes are imputed.",
        "",
        f"Model: {config.model.model}; context: {config.model.context_window}; "
        f"output budget: {config.model.max_output_tokens}; one patch per condition.",
        "",
        "Localization uses method and changed/deleted buggy-line Top-1/3/5.",
        (
            "Ties use mean rank, the best faulty-location rank is scored, and unranked "
            "faults have infinite rank."
        ),
        "Insertion-only fixes remain method-eligible; unmapped class-scope faults remain unranked.",
        "Repair pass_at_configured_k is pass@1 here: exactly one patch is generated per condition.",
        "",
    ]
    if missing:
        lines.extend(["**Incomplete selected bugs:** " + ", ".join(missing), ""])

    def number(value: object) -> str:
        return (
            "—"
            if value == "" or value is None
            else f"{value:.3f}"
            if isinstance(value, float)
            else str(value)
        )

    def table(title: str, rows: list[dict], fields: list[str]) -> None:
        lines.extend(
            [
                f"## {title}",
                "",
                "| " + " | ".join(fields) + " |",
                "| " + " | ".join("---" for _ in fields) + " |",
            ]
        )
        for row in rows:
            lines.append("| " + " | ".join(number(row[f]) for f in fields) + " |")
        lines.append("")

    for subset, label in [
        ("overall", "All evaluated bugs"),
        ("faulty_method_in_context", "At least one faulty method retrieved"),
        ("all_faulty_methods_in_context", "All faulty methods retrieved"),
    ]:
        table(
            f"Localization — {label}",
            [r for r in localizations if r["subset"] == subset],
            [
                "ranker",
                "level",
                "eligible_bugs",
                "top1",
                "top3",
                "top5",
                "unranked",
                "mean_rank",
                "mean_rank_when_ranked",
            ],
        )
    for subset, label in [
        ("overall", "All evaluated bugs"),
        ("all_faulty_methods_in_context", "Retrieval-eligible repair — all faulty methods"),
        ("faulty_method_in_context", "At least one faulty method retrieved"),
    ]:
        table(
            f"Repair — {label}",
            [r for r in repairs if r["subset"] == subset],
            [
                "condition",
                "bugs",
                "attempts",
                "plausible_bugs",
                "pass_at_configured_k",
                "valid_edit_rate",
                *[status.value for status in Status],
            ],
        )
    table(
        "Retrieval recall",
        recall_rows,
        [
            "bugs",
            "expected",
            "file_any",
            "method_any",
            "method_all",
            "line_eligible",
            "line_any",
            "line_all",
            "line_fraction",
        ],
    )
    lines.extend(
        [
            "## Protocol and interpretation",
            "",
            (
                "Each bug has two localization calls and four separate repair calls. "
                "Only localization hints vary; original context and failures are "
                "shared."
            ),
            (
                "Rankings select suspicious methods and original lines. Repair emits up "
                "to three complete replacement methods, mapped to original source "
                "ranges."
            ),
            (
                "The harness validates JSON and edit targets, parses Java syntax, "
                "compiles, runs triggering tests and then the full suite on isolated "
                "buggy checkouts."
            ),
            (
                "A plausible patch needs a full native test run, return code 0, no "
                "timeout and final Failing tests: 0. Test plausibility is not semantic "
                "correctness."
            ),
            (
                "Gold source is isolated from generation and used only for evaluation "
                "and leakage audits. No test/compiler feedback is used to revise "
                "patches."
            ),
            (
                "Context-conditioned subsets supplement the complete selected-set "
                "results and do not justify exclusions."
            ),
            (
                "Token counts are local-template counts or explicitly labeled "
                "estimates; provider usage is retained separately. "
                "Model/tokenizer/context changes may affect retrieval comparability."
            ),
            (
                "The published 60-bug set was used for tuning. Larger or stronger-model "
                "evaluations must be reported as new runs with their own selection and "
                "settings."
            ),
            "",
        ]
    )
    (output / "REPORT.md").write_text("\n".join(lines))
