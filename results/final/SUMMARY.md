# Final release evaluation

The v0.2.0 evaluation completed **60 bugs, 360 fresh model calls and 240
one-shot repair attempts** with the existing local Qwen2.5-Coder 7B. It produced
**14 test-plausible condition/bug pairs across four unique bugs**.

## Localization

| Ranker | Method Top1/3/5 (60 eligible) | Method unranked | Line Top1/3/5 (39 eligible) | Line unranked |
|---|---|---:|---|---:|
| Spectrum | 21.7% / 53.3% / 66.7% | 3 | 0% / 7.7% / 15.4% | 3 |
| LLM | 35.0% / 53.3% / 63.3% | 20 | 0% / 7.7% / 7.7% | 31 |
| Combined | 40.0% / 60.0% / 70.0% | 18 | 0% / 10.3% / 17.9% | 25 |

Every overall mean rank is infinite because unranked faults remain included.
Ranked-only method means are 66.31, 2.23 and 1.90; ranked-only line means are
609.04, 11.38 and 7.50. Parse-failure counts are 0/1/2 for spectrum/LLM/combined
and remain in the metrics. Line eligibility excludes insertion-only fixes.
Unmapped class-scope faults remain in the original method denominator.

Faulty-method-any context recall is 54/60 (90.0%); all-method recall is 50/60
(83.3%). Changed-line-any recall is 37/39 (94.9%), all-line recall is 36/39
(92.3%) and mean changed-line fraction is 93.6%.

The faulty-method-in-context localization subset uses **at least one faulty
method present** and has 54 bugs and 36 line-eligible bugs:

| Ranker | Conditional method Top1/3/5 | Method unranked | Conditional line Top1/3/5 | Line unranked |
|---|---|---:|---|---:|
| Spectrum | 24.1% / 59.3% / 74.1% | 0 | 0% / 8.3% / 16.7% | 2 |
| LLM | 38.9% / 59.3% / 70.4% | 14 | 0% / 8.3% / 8.3% | 28 |
| Combined | 44.4% / 66.7% / 77.8% | 12 | 0% / 11.1% / 19.4% | 22 |

Full conditional status, validity and mean-rank tables are in REPORT.md and the
CSVs. This subset supplements the pooled report; it does not replace the original
denominators.

## Repair

| Condition | Plausible /60 | Rate | Syntax errors | Compile errors | Trigger failures | Suite failures |
|---|---:|---:|---:|---:|---:|---:|
| Direct | 3 | 5.0% | 3 | 9 | 44 | 1 |
| Spectrum | 3 | 5.0% | 1 | 5 | 50 | 1 |
| LLM | 4 | 6.7% | 2 | 6 | 47 | 1 |
| Combined | 4 | 6.7% | 2 | 6 | 48 | 0 |

### Repair eligibility based on retrieval

For the retrieval-eligible repair analysis, a bug is included only when **all
true faulty methods are present in the frozen retrieved context**. This stricter
criterion gives **50/60 bugs (83.3%)**, or 200 attempts across the four conditions.
Of the other ten bugs, four have only some faulty methods retrieved and six have
none. All ten remain included in the primary 60-bug results above.

Eligibility is assessed after generation using sealed ground truth; it does not
guide retrieval or patch generation. This repair subset differs from the
54-bug, at-least-one-method localization subset above. Retrieving all faulty
methods does not guarantee that every supporting declaration or API needed for a
repair is available.

| Condition | Plausible / retrieval-eligible bugs | Conditional rate |
|---|---:|---:|
| Direct | 3 / 50 | 6.0% |
| Spectrum | 3 / 50 | 6.0% |
| LLM | 4 / 50 | 8.0% |
| Combined | 4 / 50 | 8.0% |

All 14 passing pairs fall within this stricter subset. These conditional rates
describe repair when retrieval includes all faulty methods; the overall rates
remain 3/60, 3/60, 4/60 and 4/60. Retrieval misses are not excluded from the main
results. Subset membership uses the `method_all` field in per-bug.csv; passing
counts were checked against the saved per-bug repair outcomes.

Chart-25, Lang-21 and Math-22 pass all four conditions; Lang-20 passes LLM and
combined. All 240 patches parsed and applied. Syntax-valid rates are 95.0%, 98.3%,
96.7% and 96.7% respectively. There are no model errors, timeouts, invalid
JSON/application outcomes or length truncations. Every failure remains counted.

Math-11's three full-suite attempts return exit code 0 without timeout but report
two failing tests, so they correctly remain FAILS_SUITE. All 14 plausible proofs
use full `defects4j test` without `-t`, return code 0, no timeout and final
`Failing tests: 0`.

## Interpretation

The evaluation pool was used for tuning, so these results are not independent
held-out evidence. Combined
localization is stronger on these method rankings, while one-shot repair remains
sparse and line Top1 remains 0%. Test plausibility is not semantic correctness.
No feedback revision, best-of sampling, stronger model, hand correction, relaxed
parser or gold-dependent retrieval was used.

## Integrity and release checks

All 360 actual prompts contain their full frozen per-bug context and fit the
28,160-input-token budget. Settings are 32,768 context, 4,096 output, greedy
seed 20261009 and one sample. The source/config/split fingerprint, frozen-context
checks and canonical original-source mappings pass. All 14 native full-suite proofs were
reverified. Disposable checkouts are removed.

Final verification: **63 tests passed in 11.00 seconds**, including 61 portable
checks and two real native integration checks; lint and formatting passed. The
v0.2.0 wheel was built and ZIP-tested. Raw evidence
and source archives remain local under `outputs/final`. See release-audit.json
for the final audit and REPORT.md/CSVs for measured tables.
