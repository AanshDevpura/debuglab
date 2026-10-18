# Defects4J debugging experiment

Evaluated 60 / 60 selected real bugs.

Results pool all evaluated selected bugs. Subset tuning was user-authorized.
Missing runs are explicitly absent; no outcomes are imputed.

Line metrics include only fixes changing/deleting buggy lines.
Method metrics include
insertion-only fixes. Ties use mean rank; an unranked fault has infinite rank.
Conditional tables use at least one faulty method fully present in context.

Repair pass@k counts a bug if any of its configured samples passes the full suite.
Invalid outputs, model failures, and timeouts remain in attempt denominators.

Results: localization.csv, repair.csv, context-recall.csv, per-bug.csv.


## Context recall (fractions)

| bugs | expected | file_any | method_any | method_all | line_eligible | line_any | line_all | line_fraction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 60 | 60 | 1 | 0.900 | 0.833 | 39 | 0.949 | 0.923 | 0.936 |

## All bugs: localization overall

| ranker | level | eligible_bugs | top1 | top3 | top5 | mean_rank | mean_rank_when_ranked | unranked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| spectrum | line | 39 | 0 | 0.077 | 0.154 | INF | 609.042 | 3 |
| spectrum | method | 60 | 0.217 | 0.533 | 0.667 | INF | 66.307 | 3 |
| llm | line | 39 | 0 | 0.077 | 0.077 | INF | 11.375 | 31 |
| llm | method | 60 | 0.350 | 0.533 | 0.633 | INF | 2.225 | 20 |
| combined | line | 39 | 0 | 0.103 | 0.179 | INF | 7.500 | 25 |
| combined | method | 60 | 0.400 | 0.600 | 0.700 | INF | 1.905 | 18 |

## All bugs: localization with faulty method in context

| ranker | level | eligible_bugs | top1 | top3 | top5 | mean_rank |
| --- | --- | --- | --- | --- | --- | --- |
| spectrum | line | 36 | 0 | 0.083 | 0.167 | INF |
| spectrum | method | 54 | 0.241 | 0.593 | 0.741 | 67.843 |
| llm | line | 36 | 0 | 0.083 | 0.083 | INF |
| llm | method | 54 | 0.389 | 0.593 | 0.704 | INF |
| combined | line | 36 | 0 | 0.111 | 0.194 | INF |
| combined | method | 54 | 0.444 | 0.667 | 0.778 | INF |

## All bugs: repair overall

| condition | bugs | attempts | plausible_bugs | pass_at_configured_k | valid_edit_rate | INVALID_FORMAT | INVALID_APPLY | SYNTAX_ERROR | COMPILE_ERROR | FAILS_TRIGGER | FAILS_SUITE | PLAUSIBLE | MODEL_ERROR | TIMEOUT |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| direct | 60 | 60 | 3 | 0.050 | 0.950 | 0 | 0 | 3 | 9 | 44 | 1 | 3 | 0 | 0 |
| spectrum | 60 | 60 | 3 | 0.050 | 0.983 | 0 | 0 | 1 | 5 | 50 | 1 | 3 | 0 | 0 |
| llm | 60 | 60 | 4 | 0.067 | 0.967 | 0 | 0 | 2 | 6 | 47 | 1 | 4 | 0 | 0 |
| combined | 60 | 60 | 4 | 0.067 | 0.967 | 0 | 0 | 2 | 6 | 48 | 0 | 4 | 0 | 0 |

## All bugs: repair with faulty method in context

| condition | bugs | plausible_bugs | pass_at_configured_k | valid_edit_rate |
| --- | --- | --- | --- | --- |
| direct | 54 | 3 | 0.056 | 0.963 |
| spectrum | 54 | 3 | 0.056 | 1 |
| llm | 54 | 4 | 0.074 | 0.981 |
| combined | 54 | 4 | 0.074 | 0.981 |

## Protocol and limitations

The user authorized tuning on subsets of this larger pool. All selected bugs
remain in the reporting denominator, presented as one pooled result.
Native screening occurred before model outcomes. Every condition receives identical
frozen context and failure information; only the localization hint varies.
Ground truth is sealed and used only for evaluation and leak audits.
Raw responses, original-numbered canonical edits, statuses and native logs are saved.
Calibration plans, decisions and source versions are saved separately from main
results. Context and completed calls are immutable; changes need separate artifacts.
Model: debuglab-qwen-coder-32k; context: 32,768; samples: 1; temperature: 0.0; seed: 20261009.
Static call proximity resolves names conservatively across overloads.
Class-scope faults without an enclosing original method are flagged as unmapped
and remain unranked in the all-bug method denominator. Fields and initializers
are statement coverage units, but not method interface edit units.
Plausible means passing the complete test suite, not proven semantic correctness.
The real handwritten Math-5 sanity check is separate from generated repair counts.
Pool tuning limits generalization claims. Future model swaps use configuration only.


## Larger-cohort safeguards and failure analysis

Candidates, native screening logs, selected list, exclusions and eligible reserve were saved before cohort model calls. The cohort uses one greedy 7B sample per condition. Existing study bugs were not reused. The whole cohort is the primary reporting population. Subset tuning was user-authorized. No failed model outcome caused an exclusion.

All saved actual calls contain the full frozen context, fit the exact configured budget, and pass the sealed leak audit. Raw prompts/responses and native logs are retained after checkout cleanup. Completed calls are never resampled.

Failure categories in failure-analysis.csv classify actual attempt outcomes. Retrieval misses are additional flags and can overlap those categories. Wrong location means a compiling failed patch does not overlap a gold faulty method; wrong fix means it does. These are proxies, not proof that an alternative repair location cannot work. Class-scope fixes without an enclosing original method remain included as unranked method faults; they are explicitly flagged as unmapped, not excluded or assigned invented methods.

All bugs: 240 actual attempts; {"compile_error": 26, "invalid_output": 8, "plausible": 14, "unmapped_fault_location": 11, "wrong_fix": 96, "wrong_location": 85}.

## What the larger-cohort results say about the hypothesis

Method Top-1 on 60 eligible larger-cohort bugs: {"combined": 0.4, "llm": 0.35, "spectrum": 0.21666666666666667}. Combined does exceed both baselines on this metric.

Line Top-1 on 39 eligible larger-cohort bugs: {"combined": 0.0, "llm": 0.0, "spectrum": 0.0}. Combined does not exceed both baselines on this metric.

Larger-cohort repair pass@1: {"combined": 0.06666666666666667, "direct": 0.05, "llm": 0.06666666666666667, "spectrum": 0.05}. These point estimates test this 7B setup, not every LLM. The same frozen context/failure data across conditions isolates localization hints. Sparse successes, invalid output, retrieval misses and method-interface limitations restrict interpretation; conditional tables show whether missing context explains failures.
