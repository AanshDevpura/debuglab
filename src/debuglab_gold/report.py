"""Aggregate only persisted real-run results; never synthesize missing observations."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from statistics import mean

from debuglab.config import Config


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def report(config: Config) -> None:
    output = (
        Path("outputs") if config.output_root == "outputs/runs" else Path(config.output_root).parent
    )
    output.mkdir(parents=True, exist_ok=True)
    split = json.loads(Path(config.split_path).read_text())
    results = [
        json.loads(path.read_text())
        for path in sorted(Path(config.output_root).glob("*/result.json"))
    ]
    evaluated = [result for result in results if result.get("evaluated")]
    rows, repair_rows, recall_rows, bug_rows = [], [], [], []
    groups = (
        {"all": split["pool"]}
        if config.allow_pool_tuning
        else {
            "pilot": split["pilot"],
            "dev": split["dev"],
            "held_out": split["held_out"],
            "larger_batch": split["pool"],
        }
    )
    for group, ids in groups.items():
        bugs = [result for result in evaluated if result["bug"] in ids]
        subsets = (
            ("overall", "faulty_method_in_context")
            if config.allow_pool_tuning
            else ("overall", "faulty_method_in_context", "all_faulty_methods_in_context")
        )
        for subset in subsets:
            subset_bugs = [
                b
                for b in bugs
                if subset == "overall"
                or b["context_recall"][
                    "method_all" if subset == "all_faulty_methods_in_context" else "method_any"
                ]
            ]
            for ranker in ("spectrum", "llm", "combined"):
                for level in ("line", "method"):
                    metrics = [
                        b["localization"][ranker]["metrics"][level]
                        for b in subset_bugs
                        if b["localization"][ranker]["metrics"][level]["eligible"]
                    ]
                    ranks = [m["rank"] for m in metrics if m["rank"] is not None]
                    misses = len(metrics) - len(ranks)
                    rows.append(
                        {
                            "batch": group,
                            "subset": subset,
                            "ranker": ranker,
                            "level": level,
                            "eligible_bugs": len(metrics),
                            "unranked": misses,
                            "parse_failures": sum(
                                bool(b["localization"][ranker].get("parse_error"))
                                for b in subset_bugs
                            ),
                            "mean_rank": "INF" if misses else (mean(ranks) if ranks else ""),
                            "mean_rank_when_ranked": mean(ranks) if ranks else "",
                            **{
                                f"top{k}": mean(m[f"top{k}"] for m in metrics) if metrics else ""
                                for k in (1, 3, 5)
                            },
                        }
                    )
            for condition in ("direct", "spectrum", "llm", "combined"):
                attempts = [a for b in subset_bugs for a in b["repair"][condition]]
                statuses = Counter(a["status"] for a in attempts)
                plausible_bugs = sum(
                    any(a["status"] == "PLAUSIBLE" for a in b["repair"][condition])
                    for b in subset_bugs
                )
                repair_rows.append(
                    {
                        "batch": group,
                        "subset": subset,
                        "condition": condition,
                        "bugs": len(subset_bugs),
                        "attempts": len(attempts),
                        "plausible_bugs": plausible_bugs,
                        "pass_at_configured_k": plausible_bugs / len(subset_bugs)
                        if subset_bugs
                        else "",
                        "applied_edit_rate": mean(
                            a["status"]
                            not in {
                                "INVALID_FORMAT",
                                "INVALID_APPLY",
                                "MODEL_ERROR",
                                "TIMEOUT",
                            }
                            for a in attempts
                        )
                        if attempts
                        else "",
                        "valid_edit_rate": mean(
                            a["status"]
                            in {
                                "COMPILE_ERROR",
                                "FAILS_TRIGGER",
                                "FAILS_SUITE",
                                "PLAUSIBLE",
                            }
                            for a in attempts
                        )
                        if attempts
                        else "",
                        **{
                            status: statuses[status]
                            for status in (
                                "INVALID_FORMAT",
                                "INVALID_APPLY",
                                "SYNTAX_ERROR",
                                "COMPILE_ERROR",
                                "FAILS_TRIGGER",
                                "FAILS_SUITE",
                                "PLAUSIBLE",
                                "MODEL_ERROR",
                                "TIMEOUT",
                            )
                        },
                    }
                )
        eligible = [b for b in bugs if b["context_recall"]["line_eligible"]]
        recall_rows.append(
            {
                "batch": group,
                "bugs": len(bugs),
                "expected": len(ids),
                **{
                    field: mean(b["context_recall"][field] for b in bugs) if bugs else ""
                    for field in ("file_any", "file_all", "method_any", "method_all")
                },
                "line_eligible": len(eligible),
                **{
                    field: mean(b["context_recall"][field] for b in eligible) if eligible else ""
                    for field in ("line_any", "line_all", "line_fraction")
                },
            }
        )
    for b in evaluated:
        bug_rows.append(
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

    def export(records: list[dict]) -> list[dict]:
        return (
            [{k: v for k, v in record.items() if k != "batch"} for record in records]
            if config.allow_pool_tuning
            else records
        )

    write_csv(output / "localization.csv", export(rows))
    write_csv(output / "repair.csv", export(repair_rows))
    write_csv(output / "context-recall.csv", export(recall_rows))
    write_csv(output / "per-bug.csv", bug_rows)
    lines = [
        "# Defects4J debugging experiment",
        "",
        f"Evaluated {len(evaluated)} / {len(split['pool'])} selected real bugs.",
        "",
        (
            "Results pool all evaluated selected bugs. Subset tuning was user-authorized."
            if config.allow_pool_tuning
            else f"The pilot contains {len(split['pilot'])} dev bugs. "
            "Larger batch combines dev and held-out."
        ),
        "Missing runs are explicitly absent; no outcomes are imputed.",
        "",
        "Line metrics include only fixes changing/deleting buggy lines.",
        "Method metrics include",
        "insertion-only fixes. Ties use mean rank; an unranked fault has infinite rank.",
        "Conditional tables use at least one faulty method fully present in context.",
        *(
            []
            if config.allow_pool_tuning
            else ["An additional subset requires every faulty method to be present."]
        ),
        "",
        "Repair pass@k counts a bug if any of its configured samples passes the full suite.",
        "Invalid outputs, model failures, and timeouts remain in attempt denominators.",
        "",
        "Results: localization.csv, repair.csv, context-recall.csv, per-bug.csv.",
        "",
    ]

    def number(value: object) -> str:
        if value == "" or value is None:
            return "—"
        return f"{value:.3f}" if isinstance(value, float) else str(value)

    def table(title: str, records: list[dict], fields: list[str]) -> None:
        lines.extend(["", f"## {title}", "", "| " + " | ".join(fields) + " |"])
        lines.append("| " + " | ".join("---" for _ in fields) + " |")
        for record in records:
            lines.append("| " + " | ".join(number(record[field]) for field in fields) + " |")

    table(
        "Context recall (fractions)",
        recall_rows,
        [
            *([] if config.allow_pool_tuning else ["batch"]),
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
    for group in ("all",) if config.allow_pool_tuning else ("pilot", "held_out", "larger_batch"):
        label = "All bugs" if config.allow_pool_tuning else group
        table(
            f"{label}: localization overall",
            [r for r in rows if r["batch"] == group and r["subset"] == "overall"],
            [
                "ranker",
                "level",
                "eligible_bugs",
                "top1",
                "top3",
                "top5",
                "mean_rank",
                "mean_rank_when_ranked",
                "unranked",
            ],
        )
        table(
            f"{label}: localization with faulty method in context",
            [r for r in rows if r["batch"] == group and r["subset"] == "faulty_method_in_context"],
            ["ranker", "level", "eligible_bugs", "top1", "top3", "top5", "mean_rank"],
        )
        table(
            f"{label}: repair overall",
            [r for r in repair_rows if r["batch"] == group and r["subset"] == "overall"],
            [
                "condition",
                "bugs",
                "attempts",
                "plausible_bugs",
                "pass_at_configured_k",
                "valid_edit_rate",
                "INVALID_FORMAT",
                "INVALID_APPLY",
                "SYNTAX_ERROR",
                "COMPILE_ERROR",
                "FAILS_TRIGGER",
                "FAILS_SUITE",
                "PLAUSIBLE",
                "MODEL_ERROR",
                "TIMEOUT",
            ],
        )
        table(
            f"{label}: repair with faulty method in context",
            [
                r
                for r in repair_rows
                if r["batch"] == group and r["subset"] == "faulty_method_in_context"
            ],
            ["condition", "bugs", "plausible_bugs", "pass_at_configured_k", "valid_edit_rate"],
        )
    lines.extend(
        [
            "",
            "## Protocol and retrieval development",
            "",
            "The pilot is a development calibration run: generic prompts, Java compatibility,",
            "and retrieval were improved on dev failures. Original pilot contexts and all model",
            "attempts remain unchanged. Held-out results use the frozen final code and config.",
            "Cold dev retrieval measurements in retrieval-calibration.json rebuild context",
            "without rerunning models; they are separate from the contexts used in these tables.",
            "A Chart-4 preparation overflow required a generic diagnostic deduplication fix",
            "during held-out execution, before any Chart-4 model call. It preserves every",
            "message and project frame, logs omissions, and leaves retrieval scores unchanged.",
            "Model config and split stayed frozen; completed contexts and calls were retained.",
            "Initial and maintenance source fingerprints document this harness correction.",
            "The handwritten Math-5 sanity patch passed the real full suite and is excluded",
            f"from model repair counts. Configured repair samples per condition: {config.samples}.",
            f"Model: {config.model.model}; context window: {config.model.context_window:,} tokens; "
            f"temperature: {config.model.temperature}; seed: {config.model.seed}.",
            "",
            "## Limitations and next steps",
            "",
            "Static call proximity conservatively resolves method names to all overloads.",
            "JaCoCo measures executable source lines; fields/initializers outside method bodies",
            "are included in statement spectra but are not method retrieval units.",
            "Plausible patches pass tests; this does not establish semantic correctness.",
            "The leak audit rejects novel fix lines and permits proven preexisting buggy lines.",
            "The small model frequently misidentifies edit endpoints; stronger models can be",
            "tested by changing config only in a new experiment with its own frozen split/run.",
            "Improve typed call resolution and whole-method budget allocation on dev bugs;",
            "retain the held-out results unchanged. A larger independently frozen bug sample",
            "is needed to assess the hypothesis beyond these four projects.",
        ]
    )
    if config.allow_pool_tuning:
        protocol_start = lines.index("## Protocol and retrieval development")
        lines = lines[:protocol_start] + [
            "## Protocol and limitations",
            "",
            "The user authorized tuning on subsets of this larger pool. All selected bugs",
            "remain in the reporting denominator, presented as one pooled result.",
            "Native screening occurred before model outcomes. Every condition receives identical",
            "frozen context and failure information; only the localization hint varies.",
            "Ground truth is sealed and used only for evaluation and leak audits.",
            "Raw responses, original-numbered canonical edits, statuses and native logs are saved.",
            "Calibration plans, decisions and source versions are saved separately from main",
            "results. Context and completed calls are immutable; changes need separate artifacts.",
            f"Model: {config.model.model}; context: {config.model.context_window:,}; "
            f"samples: {config.samples}; temperature: {config.model.temperature}; "
            f"seed: {config.model.seed}.",
            "Static call proximity resolves names conservatively across overloads.",
            "Class-scope faults without an enclosing original method are flagged as unmapped",
            "and remain unranked in the all-bug method denominator. Fields and initializers",
            (
                "have separate U repair handles for safe complete declarations/blocks; "
                "they never become method-ranking targets."
                if config.class_unit_edits
                else "are statement coverage units, but not method interface edit units."
            ),
            "Plausible means passing the complete test suite, not proven semantic correctness.",
            "The real handwritten Math-5 sanity check is separate from generated repair counts.",
            "Pool tuning limits generalization claims. Future model swaps use configuration only.",
            "",
        ]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")
