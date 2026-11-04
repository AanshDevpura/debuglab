# Does Better Localization Improve One-Shot Program Repair?

A Paired Study of 100 Defects4J Bugs

Aansh Devpura (devpura2) and Theodor Novitsky (theodor4)

## 1 Introduction (Target Problem and Motivation)

Fault localization identifies buggy code; program repair changes it to restore intended behavior. Spectrum-based localization uses passing and failing test coverage, while a large language model (LLM) interprets source and failure evidence. Combining these signals may improve diagnosis, but a repair must also preserve other behavior.

We ask: RQ1, how do spectrum-based, LLM-only and combined localization compare? RQ2, do their hints improve one-shot repair (one answer without feedback or revision) over direct generation? RQ3, how does a stronger model compare with a weaker model given the same source context? We contribute a controlled pipeline and paired measurements of localization accuracy, test plausibility and reference-based patch assessment. The code and study data are available in our repository [1].

## 2 Related Work

Statistical and combined localization. Defects4J provides real Java bugs and tests [2]; Ochiai ranks code using test coverage [3]. Zou et al. train a model to merge scores from seven localization families, each estimating how likely a statement is faulty. It ranks a faulty statement first for 72 of 357 faults, versus 24 for the best individual technique, a 200% increase [4]. Unlike this learned combination, we give spectrum ranks directly to an LLM.

LLM-based repair. Xia et al. compare nine models on five datasets [5]. On Defects4J 1.2, whole-function generation with Codex yields 102 test-passing fixes, of which 63 are judged correct. They also find that supplying the surrounding code and asking the model to fill only the missing fragment increases the proportion of correct patches. Their models sample multiple candidates; we use one answer per condition with fixed source and edit rules.

LLM-based localization. Kang et al.’s AutoFL uses test coverage and repository navigation to identify faulty methods [6]. With GPT-3.5, it improves method Top-1 accuracy over Ochiai by 19.7% on Defects4J, a relative increase. This improves diagnosis; we ask whether better rankings also improve repair when the supplied code is fixed.

Patch validity. Qi et al. find that GenProg repairs only 2 of 105 defects correctly despite 55 reported fixes; many patches remove functionality or fail proper validation [7]. Their findings motivate checking both test results and preserved behavior. We therefore report test results separately from reference-based patch assessment.

## 3 Approach and Study Methodology

DebugLab is our evaluation pipeline for localization and repair. It starts from the original buggy program, reproduces failures and collects per-test coverage. Ochiai scores a location as ef / sqrt(F(ef + ep)), where ef and ep count covering failing and passing tests, and F is the total failing-test count. Method scores use method coverage; tied spectrum scores receive mean ranks.

### 3.1 Context control and cohort construction

Retrieval selects complete buggy methods and compact declarations using failures, stack traces, lexical relevance and an unranked executed-method set, not Ochiai scores or developer fixes. Up to two uniquely resolved same-class helpers follow each caller within the existing budget. Methods receive handles and original line numbers; the selected code is frozen across conditions and models.

We select 100 of 134 inspected candidates whose retrieved code contains all known faulty methods and whose developer-provided fix fits at most three complete method replacements and Qwen’s token budgets. Model outcomes do not inform selection; fixed code never enters generation. This checks repair feasibility on a restricted cohort rather than general retrieval performance.

### 3.2 Independent localization and repair requests

We evaluate three rankers: spectrum-only (Ochiai, no LLM), LLM-only (source and failures), and combined (the same inputs plus spectrum ranks). Spectrum is computed directly; the others each require one model call. Methods and executable statements are ranked independently. Model outputs are checked for valid handles, statement starts and duplicates; invalid rankings remain failures.

![workflow](figures/workflow.png)

Figure 1. Solid arrows show source and patch flow. Dashed hints go only to LLM-guided and combined-guided repairs, independently of the shared source input.

The model generates one patch under each of four conditions: direct (no hints), spectrum-guided, LLM-guided and combined-guided. All receive the same source; the last two use their own new localization rankings. A patch replaces one to three complete original methods or constructors, preserving callable interfaces. Added fields, classes and appended helpers are rejected. Hints do not restrict edit locations; consistent parameter renaming is allowed.

### 3.3 Outcomes and comparison rules

Each patch is applied to a fresh buggy checkout, parsed, compiled, run against failure-triggering tests and then the full project suite. A pass requires defects4j test without -t, exit zero, no timeout and a final Failing tests: 0. All failed attempts remain counted. There are no retries, revisions, hand fixes or test feedback. Paired comparisons count guidance’s gains and losses on the same bugs.

### 3.4 From midpoint prototype to final study

The midpoint’s 60-bug prototype exposed missing context, unsupported edits and invalid patches. We added helper context, separate statement rankings, stricter method checks, a feasibility-screened 100-bug cohort, the stronger model and semantic review. Identical supplied code keeps the comparison about using the same information rather than obtaining more through exploration.

## 4 Evaluation (Settings and Results)

### 4.1 Experimental settings

The 100 Defects4J 3.0.1 bugs span JFreeChart (Chart, 19), Apache Commons Lang (Lang, 30), Apache Commons Math (Math, 34) and Joda-Time (Time, 17). All known faulty methods are supplied; 68 bugs have eligible changed/deleted-line targets. Both models receive the same bugs and frozen source. This new cohort is not directly comparable with the midpoint’s 60 bugs.

We compare weaker Qwen2.5-Coder 7B with stronger GPT-5.4 mini (gpt-5.4-mini-2026-03-17) [8]; these labels describe performance here. Both use temperature zero and a 4,096-token output cap. Qwen runs locally with a 32,768-token window, 28,160-token input allowance and seed 20261009. GPT uses the API without a seed or recorded reasoning tokens; its larger window adds no source.

Each model makes 600 requests and 400 repair attempts. Source and failure evidence are identical. The 400 fixed-input messages match across models; 200 repair prompts use each model’s newly generated rankings. Provider-specific request formats preserve the same edit rules and validation.

### 4.2 Results

#### 4.2.1 Localization (RQ1): no consistent gain from combining signals

Top-k measures bugs with a faulty location among the first k candidates (100 eligible bugs for methods; 68 for statements). Invalid and unranked targets are misses with infinite rank. Figure 2 compares GPT’s rankers; Table 1 includes both models.

![localization](figures/localization.png)

Figure 2. Spectrum (Ochiai) is the independent baseline; LLM-only and combined use GPT-5.4 mini. Bars show Top-k accuracy (%).

Table 1. Top-1 (%), Qwen → GPT-5.4 mini (change in points); n = 100 methods, 68 statements.

| Ranker | Method Top-1<br>Qwen → GPT-5.4 mini (Δ pp) | Statement Top-1<br>Qwen → GPT-5.4 mini (Δ pp) |
| --- | --- | --- |
| Spectrum | 30.0 → 30.0 (+0.0) | 1.5 → 1.5 (+0.0) |
| LLM-only | 48.0 → 76.0 (+28.0) | 5.9 → 32.4 (+26.5) |
| Combined | 42.0 → 77.0 (+35.0) | 4.4 → 29.4 (+25.0) |

Answer to RQ1. Qwen2.5-Coder 7B’s method Top-1 is 48% with LLM-only versus 42% combined; statement Top-1 is 5.9% versus 4.4%. GPT-5.4 mini reaches 76% versus 77% for methods and 32.4% versus 29.4% for statements. Both outperform Ochiai (30% methods, 1.5% statements). GPT’s combination gains 7 first-ranked faulty methods but loses 6 through 3 demotions and 3 invalid duplicate rankings; statement gains of 3 are offset by 5 losses. Spectrum hints can misdirect ranking: combination hurts Qwen and adds no consistent GPT benefit.

4.2.2 Repair (RQ2): guidance yields no clear aggregate advantage

With GPT-5.4 mini, direct repair passes 41/100 bugs. Spectrum guidance passes 38 (−3 percentage points), LLM guidance 41 (no change), and combined guidance 40 (−1 point). Figure 3 compares the same bugs: guidance gains some passes but loses others, explaining why better outcomes on individual bugs do not increase the overall pass rate.

![guidance](figures/guidance.png)

Figure 3. Guidance gains and losses for GPT-5.4 mini relative to direct repair on the same 100 bugs. A gain passes only with guidance; a loss passes only without it. Net changes are −3, 0 and −1 bugs.

Answer to RQ2. Hints rank code that is already available; they add neither source nor test feedback. Finding the faulty method is also insufficient: guided repair still fails to pass the suite in 38 of 76 cases where LLM-only ranks it first, and 40 of 77 such combined cases. This points to a patch-generation bottleneck: even a correct method hint often does not produce a passing change. Guidance’s gains are offset by losses in the original suite results (Figure 3); Section 4.2.4 checks whether semantic screening changes this conclusion.

A concrete example. On Time-23, LLM-only and combined both rank the faulty method and statement first. Their repairs edit that method but still fail the triggering tests; the combined patch leaves the faulty time-zone alias mappings unchanged. Direct repair changes those mappings and passes the suite. This shows how correct localization can coexist with an ineffective patch. It does not establish why the model chose that patch.

#### 4.2.3 Model capability (RQ3): stronger versus weaker

Answer to RQ3. The stronger GPT-5.4 mini passes 160/400 attempts (40.0%), versus Qwen’s 43/400 (10.75%): +117 passes, or +29.25 percentage points. The gain appears in every repair condition: +30 points for direct and LLM-guided repair, +29 for spectrum-guided, and +28 for combined-guided (Table 2). Direct repair alone rises from 11% to 41%, so the improvement does not depend on generated localization hints. LLM method Top-1 also rises from 48% to 76% (Table 1). The observed difference between models is substantially larger than the change from adding guidance.

Table 2. Suite-passing attempts on the same bugs: 100 per condition and 400 per model. The final row counts distinct bugs with at least one passing condition, not confirmed repairs.

| Repair condition | Qwen2.5-Coder 7B | GPT-5.4 mini |
| --- | --- | --- |
| Direct | 11/100 (11%) | 41/100 (41%) |
| Spectrum-guided | 9/100 (9%) | 38/100 (38%) |
| LLM-guided | 11/100 (11%) | 41/100 (41%) |
| Combined-guided | 12/100 (12%) | 40/100 (40%) |
| All attempts | 43/400 (10.75%) | 160/400 (40%) |
| Bugs with any pass | 13/100 | 49/100 |

#### 4.2.4 Human review

Review results. Human review judged 30 Qwen and 124 GPT attempts valid, and found defects in 13 and 36 suite-passing attempts (Table 3). These judgments are reported separately from the original test outcomes, which remain unchanged.

Table 3. Human-reviewed patch outcomes. Each repair condition contains 100 attempts.

| Assessment outcome | Qwen2.5-Coder 7B | GPT-5.4 mini |
| --- | --- | --- |
| Suite-passing attempts | 43 | 160 |
| Identified defects | 13 | 36 |
| Bugs with valid repairs | 9/100 | 39/100 |
| Valid repairs | 30/400 (7.5%) | 124/400 (31%) |
| Direct (no hints)<br>Spectrum-guided<br>LLM-guided<br>Combined-guided | 9/100 (9%)<br>6/100 (6%)<br>7/100 (7%)<br>8/100 (8%) | 30/100 (30%)<br>32/100 (32%)<br>31/100 (31%)<br>31/100 (31%) |

## 5 Discussion (Limitations and Future Extensions)

### 5.1 Limitations

Selection and generalization. We selected bugs whose known fixes fit our edit limits and whose faulty methods were retrieved. This controls the supplied information but excludes missing-context failures and larger fixes. Results describe this restricted cohort, not arbitrary Defects4J bugs or new projects. Public benchmark code may also appear in training data.

Model comparison and validation. One main run per model cannot establish stable one-bug differences. Provider, tokenizer and seed settings also differ, so we compare configured systems rather than isolate the cause of their performance gap. Human review strengthens validation but cannot prove correctness for every input.

### 5.2 Future extensions

Broader, repeated evaluation. Include more projects, model families and bugs excluded by current source or edit limits. Repeat each condition under the same declared budget to check whether small gains persist. Report suite-passing and human-reviewed valid repairs separately. Add tests for behavior the original suite misses, such as sort order, array types and overflow.

Better context and retrieval. Vary helper code, field definitions and relevant API information, while keeping source identical across conditions within each comparison. Measure whether extra context increases valid repairs. Evaluate retrieval on an unrestricted bug set, including missing faulty methods, to separate failure to find useful code from failure to repair supplied code.

Localization that helps repair. Compare a trained merger of spectrum and LLM scores with a policy that consults spectrum only when the LLM is uncertain. Judge both by valid repairs and Top-k accuracy. A separate diagnostic should identify known faulty methods without revealing fixes or adding source. Continued failure would isolate patch generation as the remaining obstacle.

Agentic and iterative repair. Under matched call, token and time limits, compare one-shot repair, read-only exploration, and exploration with compiler/test feedback and revision. This separates gains from more information and from revision. We excluded these workflows for cost and time, and fixed supplied context to compare use of the same evidence. Record inspected code and independently review final patches.

## 6 Conclusion

GPT-5.4 mini produces 124 human-reviewed valid repairs versus Qwen2.5-Coder 7B’s 30 out of 400 attempts each. LLM localization beats Ochiai, but combining signals adds no consistent gain. GPT’s direct repair rate is 30%, versus 31–32% with guidance: finding faulty code is insufficient when generating the edit remains difficult. Direct generation should remain a baseline, and localization should be judged by valid repairs.

## References

[1] A. Devpura and T. Novitsky. “[DebugLab: Code and Study Data.](https://github.com/AanshDevpura/debuglab)” GitHub repository, 2026.

[2] R. Just, D. Jalali and M. D. Ernst. “[Defects4J: A Database of Existing Faults to Enable Controlled Testing Studies for Java Programs.](https://doi.org/10.1145/2610384.2628055)” ISSTA, 2014.

[3] R. Abreu, P. Zoeteweij and A. J. C. van Gemund. “[An Evaluation of Similarity Coefficients for Software Fault Localization.](https://doi.org/10.1109/PRDC.2006.18)” PRDC, 2006.

[4] D. Zou, J. Liang, Y. Xiong, M. D. Ernst and L. Zhang. “[An Empirical Study of Fault Localization Families and Their Combinations.](https://homes.cs.washington.edu/~mernst/pubs/fl-families-tse2019.pdf)” IEEE Transactions on Software Engineering, 47(2), 332–347, 2021.

[5] C. S. Xia, Y. Wei and L. Zhang. “[Automated Program Repair in the Era of Large Pre-trained Language Models.](https://lingming.cs.illinois.edu/publications/icse2023a.pdf)” ICSE, 2023.

[6] S. Kang, G. An and S. Yoo. “[A Quantitative and Qualitative Evaluation of LLM-Based Explainable Fault Localization.](https://coinse.github.io/publications/pdfs/Kang2024ay.pdf)” Proceedings of the ACM on Software Engineering, FSE, 2024.

[7] Z. Qi, F. Long, S. Achour and M. Rinard. “[An Analysis of Patch Plausibility and Correctness for Generate-and-Validate Patch Generation Systems.](https://people.csail.mit.edu/rinard/paper/issta15.pdf)” ISSTA, 2015.

[8] OpenAI. “[GPT-5.4 mini.](https://developers.openai.com/api/docs/models/gpt-5.4-mini)” Model documentation; snapshot checked November 2, 2026.
