# Final 100-bug study

Read the [research report](report.md) or its
[editable Google Doc](https://docs.google.com/document/d/1prFTs354ZOPsbP989uwf3ajJfEMOTJnEU7c4Mo4OceE).
The report has five body pages and a separate reference page. This directory is the
portable evidence package for its two main runs; it does not pool earlier results.

## Main results

| Outcome | Qwen2.5-Coder 7B (weaker) | GPT-5.4 mini (stronger) |
|---|---:|---:|
| Selected bugs | 100 | 100 |
| One-shot calls / repair attempts | 600 / 400 | 600 / 400 |
| Suite-passing attempts | 43 | 160 |
| Valid repairs after review | 30 (7.5%) | 124 (31%) |
| Defective suite-passing attempts | 13 | 36 |
| Bugs with at least one valid repair | 9 | 39 |

| Valid repairs, out of 100 per condition | Qwen | GPT-5.4 mini |
|---|---:|---:|
| Direct, no hints | 9 | 30 |
| Spectrum-guided | 6 | 32 |
| LLM-guided | 7 | 31 |
| Combined-guided | 8 | 31 |

Method Top-1 for Ochiai / LLM-only / combined is **30 / 48 / 42** for Qwen and
**30 / 76 / 77** for GPT. Statement Top-1 has 68 eligible bugs: **1 / 4 / 3**
versus **1 / 22 / 20**. See [summary.json](summary.json) for every Top-1/3/5 count.

LLM localization outperforms Ochiai here. Combining signals is worse for Qwen and
adds no consistent gain for GPT. GPT yields substantially more valid repairs, but
localization hints add little over direct generation when faulty code is already
supplied. These are observations on this selected cohort, not isolated causal
estimates of model architecture or proof of general superiority.

## Evidence contents

- `evidence/data.json`: 1,200 per-bug/level/ranker localization rows, 800 repair
  outcomes, 200 input-hash records, 600 paired request comparisons and model settings.
- `evidence/patches/`: all 96 within-run patch groups (95 globally unique patches),
  covering the 203 suite-passing attempts. Each includes original/generated/reference
  method text, conditions, assessment and rationale.
- `evidence/manifest.json`: SHA-256 for every exported evidence file.
- `summary.json`: the offline verifier's aggregate output.
- `report.md` and `figures/`: readable final report and the three report figures.

Native proof summaries for passing attempts record the original log hash, full-suite
command, return code, timeout flag and last failing-test count. They are derived
summaries, not the complete native stdout. Raw prompts, provider responses, source
archives and full native logs remain in the original ignored run directories; this
package does not claim to contain enough raw evidence to rerun every original audit.
It does contain every row needed to recompute the paper's reported result totals.
Source-archive and raw-call hashes preserve links to the retained original evidence.

## Reproduce the numbers

From repository root, with Python 3.11+ and no external dependencies/network:

```sh
python3 scripts/verify-study-data.py
```

The verifier checks all denominators and pair identities, manifests, normalized
schema equality, fixed-input request equality, per-condition review assignments,
full-suite proof summaries, Top-k counts and final totals. Editing evidence causes
verification to fail. It is not a substitute for running the benchmark or reviewing
patch semantics. See the root README to install and rerun the harness.

To rebuild the export from retained private run artifacts, install the project and
use a **new** destination (the exporter refuses overwrite):

```sh
python scripts/export-study-data.py \
  --qwen outputs/final-retrieval-certified-100 \
  --gpt outputs/hosted-gpt54mini-100-compatible \
  --review outputs/final-course-report/revision15/VALIDATION_LEDGER.json \
  --output outputs/study-export-new
```

This command only reads saved artifacts. It requires the full retained run roots;
those input paths are historical local artifacts, not files downloaded by a clone.
No paid calls or model retries occur during export or verification.

## Review provenance and limits

Review compared every passing patch against original code, the developer's fix,
other changed methods and documented behavior; targeted native checks investigated
suspected regressions. Matching a reference fix supports validity, but alternative
implementations can also be valid. Results classify attempts, including repeated
identical patches across conditions, without selecting a best answer.

The user confirmed human revalidation of the initial assessment on November 3,
2026. Subsequent AI-assisted source/contract review and targeted tests resolved the
remaining 12 attempts: four supported and eight defective. That later adjudication
is not represented as a new independent human sign-off. The per-patch rationales
record the basis, including source-based portability judgments where a historical
JVM behavior was not reproduced. Validity is a reasoned review judgment, not formal
proof for all inputs; the original native `PLAUSIBLE` statuses remain unchanged.

The cohort is reference-and-retrieval-conditioned (Chart 19, Lang 30, Math 34,
Time 17), with all known faulty methods supplied. Of 134 inspected candidates,
100 met the study's context/edit/budget eligibility rules. Generalization beyond
this cohort is limited. Model/provider/tokenizer/seed/schema encoding differ;
400 fixed-input message pairs and all 100 contexts match, while 200 guided repair
prompts depend on each model's own new localization outputs. No pooling or best-of
success claim is made. Agentic exploration and feedback were excluded for cost,
time and control over supplied information; they remain future work.

Benchmark fragments retain their original upstream licensing; see
[Defects4J](https://github.com/rjust/defects4j) and the corresponding JFreeChart,
Apache Commons Lang/Math and Joda-Time projects. The earlier 60-bug package in
`results/final/` remains separate historical evidence.
