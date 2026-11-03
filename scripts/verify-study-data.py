"""Verify and summarize the final-report evidence, offline with the Python standard library."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

CONDITIONS = ("direct", "spectrum", "llm", "combined")


def verify(root: Path) -> dict:
    evidence = root / "evidence"
    manifest = json.loads((evidence / "manifest.json").read_text())
    for name, expected in manifest.items():
        path = evidence / name
        if not path.resolve().is_relative_to(evidence.resolve()):
            raise ValueError("Unsafe evidence path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Changed evidence: {name}")
    d = json.loads((evidence / "data.json").read_text())
    bugs = d["bugs"]
    assert len(bugs) == len(set(bugs)) == 100
    assert Counter(b.split("-")[0] for b in bugs) == {
        "Chart": 19,
        "Lang": 30,
        "Math": 34,
        "Time": 17,
    }
    assert len(d["requests"]) == 600
    assert len({(r["bug"], r["call"]) for r in d["requests"]}) == 600
    assert sum(r["fixed_input"] for r in d["requests"]) == 400
    assert all(r["messages_equal"] for r in d["requests"] if r["fixed_input"])
    assert all(r["normalized_schema_equal"] for r in d["requests"])
    assert len(d["inputs"]) == 200
    inputs = {(r["model"], r["bug"]): r for r in d["inputs"]}
    for bug in bugs:
        a, b = inputs["qwen", bug], inputs["gpt", bug]
        assert a["context_sha256"] == b["context_sha256"]
        assert a["failure_sha256"] == b["failure_sha256"]
        assert a["all_faulty_methods_in_context"] and b["all_faulty_methods_in_context"]
    assert len(d["localization"]) == 1200
    assert len({(r["model"], r["bug"], r["ranker"], r["level"]) for r in d["localization"]}) == 1200
    assert len(d["attempts"]) == 800
    assert len({(r["model"], r["bug"], r["condition"]) for r in d["attempts"]}) == 800
    reviews = {p.stem: json.loads(p.read_text()) for p in (evidence / "patches").glob("*.json")}
    assert len(reviews) == 96
    summary = {
        "bugs": 100,
        "context_pairs": 100,
        "fixed_messages_equal": 400,
        "normalized_schemas_equal": 600,
        "models": {},
    }
    for model in ("qwen", "gpt"):
        assert d["models"][model]["calls"] == 600
        attempts = [a for a in d["attempts"] if a["model"] == model]
        assert len(attempts) == 400
        valid = [a for a in attempts if a["review"] == "supported"]
        passed = [a for a in attempts if a["status"] == "PLAUSIBLE"]
        for a in attempts:
            if a["status"] != "PLAUSIBLE":
                assert a["review"] is None and a["review_id"] is None
                continue
            r = reviews[a["review_id"]]
            assert r["model"] == model and r["bug"] == a["bug"]
            assert a["condition"] in r["conditions"] and r["assessment"] == a["review"]
            assert a["review"] in {"supported", "identified_defect"}
            assert a["native_proofs"]
            for p in a["native_proofs"]:
                assert p["command"] == ["defects4j", "test"]
                assert p["returncode"] == p["last_failing_tests"] == 0 and not p["timed_out"]
        metrics = {}
        for ranker in ("spectrum", "llm", "combined"):
            for level in ("method", "line"):
                rows = [
                    r
                    for r in d["localization"]
                    if r["model"] == model
                    and r["ranker"] == ranker
                    and r["level"] == level
                    and r["eligible"]
                ]
                assert len(rows) == (100 if level == "method" else 68)
                metrics[ranker + "_" + level] = {
                    "eligible": len(rows),
                    "top1_3_5": [sum(r[f"top{k}"] for r in rows) for k in (1, 3, 5)],
                }
        summary["models"][model] = {
            "suite_passing": len(passed),
            "valid": len(valid),
            "defective": sum(a["review"] == "identified_defect" for a in passed),
            "distinct_valid_bugs": len({a["bug"] for a in valid}),
            "suite_passing_by_condition": [
                sum(a["condition"] == c for a in passed) for c in CONDITIONS
            ],
            "valid_by_condition": [sum(a["condition"] == c for a in valid) for c in CONDITIONS],
            "statuses": dict(sorted(Counter(a["status"] for a in attempts).items())),
            "localization": metrics,
        }
    # These are the final report's declared totals, checked against all individual rows.
    for model, expected in [("qwen", (43, 30, 13)), ("gpt", (160, 124, 36))]:
        s = summary["models"][model]
        assert (s["suite_passing"], s["valid"], s["defective"]) == expected
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("results/study-100"))
    args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2))


if __name__ == "__main__":
    main()
