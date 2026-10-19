# Evaluation results

## Evaluation setup

The evaluation completed **60 selected Defects4J bugs, 360 model calls and 240
one-shot repair attempts** using local Qwen2.5-Coder 7B. The selected bugs cover
Lang, Math, Chart and Time; their IDs are in
[`configs/bugs.json`](../../configs/bugs.json).

Each bug receives two localization requests (LLM-only and combined) and four
independent repair requests (direct, spectrum-guided, LLM-guided and
combined-guided). Every condition shares the same frozen source context and
failure information. Only localization guidance changes between repair conditions.
Each repair request produces one patch of up to three complete method replacements.
Validation checks edit mappings, Java syntax, compilation, triggering tests and
the complete native suite on an isolated buggy checkout.

| Setting | Value |
|---|---|
| Benchmark | Defects4J 3.0.1 |
| Model | Qwen2.5-Coder 7B |
| Context / output budget | 32,768 / 4,096 tokens |
| Input limit | 28,160 tokens |
| Temperature / seed | 0 / 20261009 |
| Generated patches | One per bug per repair condition |

Ground-truth fixes are used only for evaluation and leakage checks. Generated
patches receive no compiler/test feedback revision or manual correction.

## Localization

Method metrics include all 60 bugs. Line metrics include 39 bugs whose fixes
change or delete buggy lines; insertion-only fixes remain method-eligible.
A bug's rank is its best-ranked true faulty location, with mean ranks for ties.
Unranked faults have infinite rank and remain in the denominator. Ranked-only
means summarize just the faults assigned a rank.

### All selected bugs

| Ranker | Level | Eligible bugs | Top-1 | Top-3 | Top-5 | Unranked | Mean rank | Ranked-only mean |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Spectrum | Method | 60 | 21.7% | 53.3% | 66.7% | 3 | ∞ | 66.31 |
| Spectrum | Line | 39 | 0.0% | 7.7% | 15.4% | 3 | ∞ | 609.04 |
| LLM | Method | 60 | 35.0% | 53.3% | 63.3% | 20 | ∞ | 2.23 |
| LLM | Line | 39 | 0.0% | 7.7% | 7.7% | 31 | ∞ | 11.38 |
| Combined | Method | 60 | 40.0% | 60.0% | 70.0% | 18 | ∞ | 1.90 |
| Combined | Line | 39 | 0.0% | 10.3% | 17.9% | 25 | ∞ | 7.50 |

Combined localization ranks a faulty method first on **24/60 bugs (40.0%)**.
All three rankers have line Top-1 accuracy of 0%. Localization parse failures are
0 for spectrum, 1 for LLM and 2 for combined; these outcomes remain included.
Class-scope faults without an enclosing original method remain unranked.

### Faulty method present in the retrieved context

This conditional subset includes bugs with **at least one true faulty method
present in the frozen retrieved context**: 54 bugs, including 36 line-eligible
bugs. It supplements the complete selected-set results.

| Ranker | Level | Eligible bugs | Top-1 | Top-3 | Top-5 | Unranked | Mean rank | Ranked-only mean |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Spectrum | Method | 54 | 24.1% | 59.3% | 74.1% | 0 | 67.84 | 67.84 |
| Spectrum | Line | 36 | 0.0% | 8.3% | 16.7% | 2 | ∞ | 619.10 |
| LLM | Method | 54 | 38.9% | 59.3% | 70.4% | 14 | ∞ | 2.23 |
| LLM | Line | 36 | 0.0% | 8.3% | 8.3% | 28 | ∞ | 11.38 |
| Combined | Method | 54 | 44.4% | 66.7% | 77.8% | 12 | ∞ | 1.90 |
| Combined | Line | 36 | 0.0% | 11.1% | 19.4% | 22 | ∞ | 7.50 |

## Retrieval coverage

| Measure | Result |
|---|---:|
| At least one faulty method present | 54/60 (90.0%) |
| All faulty methods present | 50/60 (83.3%) |
| At least one changed/deleted faulty line present | 37/39 (94.9%) |
| All changed/deleted faulty lines present | 36/39 (92.3%) |
| Mean fraction of faulty lines present | 93.6% |

Retrieval eligibility is determined after generation using sealed ground truth;
it does not guide source selection or patch generation. Retrieval misses remain
in the main 60-bug denominator.

## Repair

A patch is **test-plausible** only when the complete `defects4j test` command
(without `-t`) returns code 0, does not time out and reports final
`Failing tests: 0`.

| Condition | Plausible / 60 | Rate | Syntax errors | Compile errors | Trigger failures | Suite failures |
|---|---:|---:|---:|---:|---:|---:|
| Direct | 3 | 5.0% | 3 | 9 | 44 | 1 |
| Spectrum-guided | 3 | 5.0% | 1 | 5 | 50 | 1 |
| LLM-guided | 4 | 6.7% | 2 | 6 | 47 | 1 |
| Combined-guided | 4 | 6.7% | 2 | 6 | 48 | 0 |

The **14 passing condition/bug pairs cover four unique bugs**:

| Bug | Passing conditions |
|---|---|
| Chart-25 | Direct, spectrum, LLM, combined |
| Lang-21 | Direct, spectrum, LLM, combined |
| Math-22 | Direct, spectrum, LLM, combined |
| Lang-20 | LLM, combined |

All 240 patches parsed and applied. Syntax-valid rates are 95.0%, 98.3%, 96.7%
and 96.7% for direct, spectrum, LLM and combined respectively. There were no
model errors, timeouts, invalid-format/application outcomes or length truncations.
Math-11's three full-suite failures returned exit code 0 but reported two failing
tests, so they remain suite failures.

### Repair eligibility based on retrieval

Repair eligibility requires **all true faulty methods to be present in the
frozen retrieved context**. This includes 50/60 bugs and 200 attempts across four
conditions. Four other bugs have some faulty methods present and six have none.
All ten remain in the overall results.

| Condition | Plausible / 50 retrieval-eligible bugs | Conditional rate |
|---|---:|---:|
| Direct | 3 / 50 | 6.0% |
| Spectrum-guided | 3 / 50 | 6.0% |
| LLM-guided | 4 / 50 | 8.0% |
| Combined-guided | 4 / 50 | 8.0% |

All passing pairs fall within this subset. For the broader 54-bug subset with
at least one faulty method present, repair rates are 3/54 (5.6%), 3/54 (5.6%),
4/54 (7.4%) and 4/54 (7.4%) respectively. The all-method criterion is the primary
retrieval-eligible repair analysis because multi-method repairs can require
several faulty methods to be editable.

## Limitations and interpretation

- **Limited coverage:** These results describe one local 7B model on 60 selected
  Java bugs from four projects. Stronger hosted models and larger evaluation sets
  require separate measurements; these outcomes do not establish performance
  across other models, projects or languages.
- **Sparse repair successes:** Four unique bugs pass in at least one condition.
  The 3-versus-4 passing counts provide limited evidence for a repair advantage;
  better method rankings do not consistently translate into successful patches.
- **Imprecise line localization:** Line Top-1 is 0% for every ranker. Method-level
  gains therefore do not demonstrate precise statement-level diagnosis.
- **Context limits:** Six bugs have no faulty method retrieved, and another four
  have only some. Even complete faulty-method retrieval may omit supporting
  declarations or API information needed for a valid repair.
- **Edit scope:** Patches replace complete methods or constructors. Field and
  initializer faults can fall outside this interface; unmapped faults remain
  included in the results.
- **Test plausibility:** Passing the available suite does not prove semantic
  correctness or rule out overfitting to the tests.
- **One-shot measurement:** A single generated patch per condition measures this
  protocol, rather than the potential of iterative repair. Greedy decoding and a
  fixed seed do not guarantee identical responses across service executions.
- **Conditional results:** Retrieval-conditioned rates describe selected subsets;
  they supplement, rather than replace, the overall rates.

## Evidence and validation

All 360 actual prompts contained the full frozen per-bug context and fit the
configured input budget. Frozen-context hashes, source/configuration fingerprints
and canonical original-source mappings passed integrity checks. All 14 plausible
native full-suite proofs were reverified, and disposable checkouts were removed.
The [README](../../README.md) explains reproduction and reporting.

### Supporting run output

This page is the single narrative report. The [evidence folder](evidence/) contains
saved machine-readable output for checking the tables and individual outcomes:

| Evidence | What it establishes |
|---|---|
| [Localization CSV](evidence/localization.csv) | Overall and context-conditional ranks, Top-1/3/5 and eligibility |
| [Repair CSV](evidence/repair.csv) | Per-condition counts, rates and validation statuses |
| [Per-bug CSV](evidence/per-bug.csv) | Individual bug outcomes and retrieval eligibility |
| [Context recall CSV](evidence/context-recall.csv) | Aggregate retrieval coverage |
| [Per-bug records](evidence/per-bug/) | Saved metrics, retrieval flags, generated edits and repair statuses for all 60 bugs |
| [Native validation logs](evidence/native-logs/) | Recorded compilation and test commands, output, exit codes and timeouts across all repair attempts |
| [Repair failure analysis](evidence/failure-analysis.csv) and [localization failure analysis](evidence/localization-failure-analysis.csv) | Per-attempt and per-ranker diagnostic classifications |
| [Final audit](evidence/final-audit.json) | Run counts, context hashes and original full-suite proof hashes |
| [Verification output](evidence/VERIFICATION.txt) | Evaluation-time test, lint and formatting output |
| [Evidence manifest](evidence/manifest.json) | SHA-256 hashes for published files and their original source artifacts |

Machine-specific paths are normalized in published records. The manifest records
both original and published hashes and identifies transformations. Per-bug records
retain measured metrics and repair records, while the full ranking arrays and raw
model prompts/responses remain locally in `outputs/final/`. These files document
the recorded run; no model generations or native tests were repeated to create
this evidence bundle.

The evaluated release passed 63 checks (61 portable and 2 native integration
checks), plus lint and formatting. This is evaluation-time validation; the current
codebase's verification commands are documented in the repository README.
