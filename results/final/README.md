# Final evaluation results

Start with [SUMMARY.md](SUMMARY.md). The selected 60-bug evaluation completed
360 model calls and 240 one-shot repair attempts, producing 14 test-plausible
condition/bug pairs across four unique bugs.

- `REPORT.md` and CSVs: pooled localization, repair, retrieval and failure metrics.
- `SUMMARY.md`: interpretation and the stricter all-faulty-methods-retrieved repair subset.
- `comparison.json`: provenance comparison metadata for the completed evaluation.
- `final-audit.json` and `release-audit.json`: counts, native proof hashes and integrity.
- `VERIFICATION.txt`: native test, lint and formatting verification.

Paths in audit records refer to ignored local experiment evidence. Raw model calls,
native logs, tools, debugging-input bundles and source archives are not distributed
in Git. The repository README explains installation, reproduction and the protocol.
