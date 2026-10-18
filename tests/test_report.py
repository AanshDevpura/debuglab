import csv
import json
from dataclasses import replace
from pathlib import Path

from debuglab.config import Config
from debuglab.io import save
from debuglab_gold.report import report


def result(bug, *, ranked, all_retrieved, line_eligible):
    metric = {
        "rank": 1 if ranked else None,
        "eligible": True,
        "top1": ranked,
        "top3": ranked,
        "top5": ranked,
    }
    return {
        "bug": bug,
        "context_hash": "frozen",
        "evaluated": True,
        "pipeline_complete": True,
        "gold_eligibility": {"method_target_mapped": True},
        "leak_violations": [],
        "context_recall": {
            "file_any": True,
            "file_all": True,
            "method_any": True,
            "method_all": all_retrieved,
            "line_eligible": line_eligible,
            "line_any": True if line_eligible else None,
            "line_all": True if line_eligible else None,
            "line_fraction": 1.0 if line_eligible else None,
        },
        "localization": {
            ranker: {
                "parse_error": None,
                "metrics": {"method": metric, "line": dict(metric, eligible=line_eligible)},
            }
            for ranker in ("spectrum", "llm", "combined")
        },
        "repair": {
            condition: [{"status": "PLAUSIBLE" if ranked else "COMPILE_ERROR"}]
            for condition in ("direct", "spectrum", "llm", "combined")
        },
    }


def test_reports_pool_one_selection_and_preserve_missing_unranked_and_all_method_subset(tmp_path):
    config = replace(
        Config.load(Path("tests/fixtures/config.json")),
        bugs_path=str(tmp_path / "bugs.json"),
        output_root=str(tmp_path / "runs"),
    )
    save(Path(config.bugs_path), ["Lang-1", "Cli-1", "Math-1"])
    save(
        Path(config.output_root) / "Lang-1/result.json",
        result("Lang-1", ranked=True, all_retrieved=True, line_eligible=True),
    )
    save(
        Path(config.output_root) / "Cli-1/result.json",
        result("Cli-1", ranked=False, all_retrieved=False, line_eligible=False),
    )
    # A result outside the selected list must not inflate results.
    save(
        Path(config.output_root) / "Math-2/result.json",
        result("Math-2", ranked=True, all_retrieved=True, line_eligible=True),
    )
    report(config)
    completion = json.loads((tmp_path / "completion.json").read_text())
    assert completion == {"expected": 3, "evaluated": 2, "missing": ["Math-1"], "complete": False}
    with (tmp_path / "localization.csv").open() as handle:
        localizations = list(csv.DictReader(handle))
    overall = next(
        r
        for r in localizations
        if r["subset"] == "overall" and r["ranker"] == "combined" and r["level"] == "method"
    )
    assert overall["eligible_bugs"] == "2" and overall["unranked"] == "1"
    assert overall["mean_rank"] == "INF" and float(overall["mean_rank_when_ranked"]) == 1
    assert float(overall["top1"]) == 0.5
    with (tmp_path / "repair.csv").open() as handle:
        repairs = list(csv.DictReader(handle))
    overall = next(r for r in repairs if r["subset"] == "overall" and r["condition"] == "direct")
    strict = next(
        r
        for r in repairs
        if r["subset"] == "all_faulty_methods_in_context" and r["condition"] == "direct"
    )
    assert overall["bugs"] == "2" and overall["COMPILE_ERROR"] == "1"
    assert overall["plausible_bugs"] == "1" and float(overall["pass_at_configured_k"]) == 0.5
    assert strict["bugs"] == strict["plausible_bugs"] == "1"
    text = (tmp_path / "REPORT.md").read_text()
    assert "Math-1" in text and "Partial-table rates" in text
    assert text.index("## Localization") < text.index("## Repair")
    assert "held-out" not in text
