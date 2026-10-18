# DebugLab

DebugLab evaluates LLM-assisted fault localization and automated program repair on
real Java bugs from Defects4J. It compares spectrum-based localization, LLM-only
localization and their combination, then checks one generated patch per repair
condition with real compilation and tests.

There is **one selected evaluation set**, stored as a flat list in
[`configs/bugs.json`](configs/bugs.json). There are no pilot, development or test
partitions. The intended extensions are a stronger hosted model and a larger bug
list; both use configuration rather than a new pipeline.

## Measured localization results

The published evaluation used local Qwen2.5-Coder 7B on 60 selected bugs from Lang,
Math, Chart and Time. These measured results are preserved in
[`results/final`](results/final); the runner refactor does not replace them with
new or simulated outcomes.

| Ranker | Method Top-1 | Method Top-3 | Method Top-5 | Line Top-1 | Line Top-3 | Line Top-5 |
|---|---:|---:|---:|---:|---:|---:|
| Spectrum / Ochiai | 21.7% | 53.3% | 66.7% | 0.0% | 7.7% | 15.4% |
| LLM | 35.0% | 53.3% | 63.3% | 0.0% | 7.7% | 7.7% |
| Combined | 40.0% | 60.0% | 70.0% | 0.0% | 10.3% | 17.9% |

Method metrics include all 60 bugs. Line metrics have 39 eligible bugs, defined by
changed or deleted buggy lines; insertion-only fixes remain method-eligible.
Unranked faults remain counted: method misses are 3/20/18 and line misses are
3/31/25 for spectrum/LLM/combined. Overall mean ranks are therefore infinite;
ranked-only means are reported separately. Ties receive mean rank, and a bug is
scored using its best-ranked true faulty location.

Combined localization places a faulty method first on 24/60 bugs. Precise line
localization remains difficult: all three rankers have line Top-1 accuracy of 0%.
The selected set was used for tuning, so these are not independent generalization
or causal claims.

## Measured repair results

The same evaluation completed 360 model calls and 240 one-shot repair attempts.

| Repair condition | Plausible / 60 | Overall rate | Plausible / 50 retrieval-eligible bugs |
|---|---:|---:|---:|
| Direct | 3 | 5.0% | 3 / 50 (6.0%) |
| Spectrum-guided | 3 | 5.0% | 3 / 50 (6.0%) |
| LLM-guided | 4 | 6.7% | 4 / 50 (8.0%) |
| Combined-guided | 4 | 6.7% | 4 / 50 (8.0%) |

These are **14 passing condition/bug pairs across four unique bugs**, not 14
repaired bugs. Chart-25, Lang-21 and Math-22 pass all four conditions; Lang-20
passes with LLM and combined hints. A test-plausible patch passes the available
suite; this does not prove semantic correctness.

The stricter repair subset requires **all true faulty methods to be retrieved**
(50/60 bugs). Localization additionally reports the subset with at least one
faulty method retrieved (54/60). Both supplement the complete selected-set results;
retrieval misses are never removed from the main denominator. See the
[summary](results/final/SUMMARY.md), [detailed report](results/final/REPORT.md),
[localization tables](results/final/localization.csv) and
[repair tables](results/final/repair.csv).

## What the LLM actually does

### 1. Build a shared debugging input

The harness checks out the original buggy version, reproduces failing tests and
collects per-test executable-line coverage. Ochiai converts coverage into
suspiciousness rankings without using an LLM.

Separately, retrieval selects relevant complete methods and compact class
information using failing-test text, stack traces, lexical matches, an unranked
executed-method set and conservative call proximity. Retrieval cannot read Ochiai
scores or known fixes. The selected source is numbered, assigned short method
handles such as `M001`, budgeted and frozen once per bug.

### 2. Ask for fault rankings

The configured model receives two separate localization requests:

- **LLM-only:** source context plus failing-test/error information.
- **Combined:** the same input plus spectrum-based suspiciousness hints.

The model returns JSON ranking up to ten methods, with up to five original line
numbers inside each method. For example:

```json
{"methods": [{"method_id": "M001", "lines": [123, 126]}]}
```

The parser checks handles, line ranges and duplicates, then converts the response
into original method and statement rankings. Invalid rankings remain recorded
failures. Ground-truth fixes are used only by a separate evaluation process.

### 3. Ask for four independent patches

The same configured model makes four **separate repair calls**. Every call gets
the same frozen source and failure information. Only its localization hint changes:

| Condition | Input added to the repair request |
|---|---|
| Direct | No explicit localization ranking |
| Spectrum-guided | Ochiai method/statement rankings |
| LLM-guided | The validated LLM-only rankings |
| Combined-guided | The validated combined rankings |

Rankings are guidance, not forced edit locations. The model may choose any complete
method available in the shared context. It returns one JSON patch containing one
to three distinct method handles and **complete replacement Java methods or
constructors**, including their original signatures and braces. It does not run
shell commands, edit files itself or receive compiler/test feedback.

The harness resolves each handle to its original production file and inclusive
line range. It replaces those method bodies and declarations with the generated
strings, applying ranges from the bottom of a file upward so line numbers do not
drift. No test edits, arbitrary paths, duplicate handles or overlapping ranges are
accepted. Generated source is not hand-corrected.

### 4. Check each patch on an isolated checkout

Each condition starts from a fresh copy of the original buggy program. Validation
stops at the first failed stage:

1. Parse JSON and validate the original-source edit mappings.
2. Parse the modified Java source to check syntax.
3. Compile with the benchmark's native build.
4. Run the original triggering tests.
5. Run the **complete** native test suite.

A patch is `PLAUSIBLE` only if the full `defects4j test` command, without `-t`,
returns code 0, does not time out and reports a final `Failing tests: 0`. Exit code
0 alone is insufficient. Failures are classified as `INVALID_FORMAT`,
`INVALID_APPLY`, `SYNTAX_ERROR`, `COMPILE_ERROR`, `FAILS_TRIGGER`, `FAILS_SUITE`,
`MODEL_ERROR` or `TIMEOUT`. All remain in the results. There is no feedback-driven
revision, repeated sampling or best-of selection.

## Install

Use Python 3.11 and Java 11 on macOS or Linux. Native experiments also need Git,
Subversion, Perl and `cpanm`, as documented by
[Defects4J](https://github.com/rjust/defects4j#setting-up-defects4j). The measured
benchmark version is 3.0.1.

```sh
git clone https://github.com/AanshDevpura/debuglab.git
cd debuglab
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-lock.txt
pip install --no-deps -e .

mkdir -p work/tools
git clone https://github.com/rjust/defects4j.git work/tools/defects4j
(
  cd work/tools/defects4j
  cpanm --installdeps .
  ./init.sh
)
```

Set `JAVA_HOME` and `PATH` to your Java 11 installation. Check that Defects4J reports
version 3.0.1 when reproducing the measured results. The default executable is
`work/tools/defects4j/framework/bin/defects4j`; another path can be configured.

Install the coverage dependencies at these filenames:

```sh
mkdir -p work/tools/jacoco
curl --fail --location \
  https://repo.maven.apache.org/maven2/org/jacoco/org.jacoco.agent/0.8.12/org.jacoco.agent-0.8.12-runtime.jar \
  --output work/tools/jacoco/agent.jar
curl --fail --location \
  https://repo.maven.apache.org/maven2/org/jacoco/org.jacoco.cli/0.8.12/org.jacoco.cli-0.8.12-nodeps.jar \
  --output work/tools/jacoco/cli.jar

source scripts/env.sh
java -version
defects4j info -p Lang
```

The environment script discovers optional workspace-local tools and sets the
benchmark timezone to `America/Los_Angeles`. Always use this timezone for native
runs. Python dependencies are pinned; model weights and native tools are separate.

## Run

### Local 7B model

Install and start [Ollama](https://docs.ollama.com/quickstart), then create the alias:

```sh
ollama pull qwen2.5-coder:7b
ollama create debuglab-qwen-coder-32k -f configs/ollama-7b.Modelfile
```

Run **every bug in the selected list** with one command:

```sh
debuglab run
```

Or run just one selected bug in a separate output directory:

```sh
debuglab run --bug Chart-25 --output outputs/smoke
```

A full run submits six model requests per bug: two localizations and four repairs.
The default output directory is `outputs/local-7b`. Settings preserve the local
32,768-token context, 4,096-token output budget, 512-token safety reserve,
temperature 0 and seed 20261009. The matching tokenizer supplies local chat-template
counts; the input limit is 28,160 tokens.

### Hosted stronger model

Copy [`configs/hosted.example.json`](configs/hosted.example.json) to an ignored
local configuration, then edit the provider details:

```sh
cp configs/hosted.example.json configs/hosted.local.json
```

Set `base_url` to a Chat Completions-compatible endpoint, `model` to the hosted
model ID, and the context/output budgets to values supported by that model. Put
credentials in the environment variable named by `api_key_env`:

```sh
read -s DEBUGLAB_API_KEY
export DEBUGLAB_API_KEY
debuglab run --config configs/hosted.local.json
```

The example uses `tokenizer: "conservative"`: UTF-8 byte counts plus estimated
message framing, explicitly labeled **estimates**, without downloading a tokenizer.
For an open-weight hosted model, set `tokenizer` to its matching Hugging Face
identifier or local tokenizer directory to use its chat template instead. The
server may use a different template; reported token usage and discrepancies are
retained separately. The estimator cannot guarantee an unknown server's framing.
A response reporting an input-budget overflow remains a failed attempt, not a retry.

Provider controls are limited to request compatibility: `token_parameter` can be
`max_tokens` or `max_completion_tokens`; `seed` and `temperature` can be `null` to
omit them. Enable `json_mode` or `schema_mode` only if supported by the provider.
Strict local parsing remains enabled regardless of server formatting support.
There is no automatic fallback request or resampling after an API error.

### More bugs

Edit the flat JSON list in `configs/bugs.json`, or pass a different list:

```sh
debuglab run --bugs configs/more-bugs.json --output outputs/more-bugs
```

Lists contain Defects4J IDs such as `["Lang-1", "Math-3", "Cli-1"]`; duplicates
and unsafe IDs are rejected. There is no fixed set size or project whitelist.
Additional projects must still reproduce successfully with the benchmark toolchain;
preparation failures stay selected and appear as incomplete, not silently excluded.

Changing the model, bug list or source requires a **new output directory**. All
four repair conditions still share context within a bug. A different model,
tokenizer or budget can change which methods fit; compare context hashes and
retrieval recall when interpreting cross-model results.

### Resume and report

Repeat the **same run command** to resume. The runner locks the output directory,
freezes the configuration, bug list and source before calls, skips completed bugs
and preserves request caches. Never delete fingerprints or regenerate an
interrupted/completed call. A pending request without a saved response becomes an
explicit failure rather than permission to resample.

To regenerate reports from saved evidence, without model calls:

```sh
debuglab report --output outputs/local-7b
```

Reports contain pooled method/line Top-1/3/5, infinite and ranked-only mean ranks,
context recall, syntax-validity rates, all repair statuses, missing selected bugs,
and both context-conditioned subsets. Incomplete runs are explicitly labeled;
partial rates are not presented as completed-set results.

## Verify

```sh
# Portable checks; no model or benchmark installation needed
ruff check .
ruff format --check .
pytest -q -m 'not integration'
scripts/check-release

# Includes real Java/Defects4J integration checks after native setup
source scripts/env.sh
scripts/verify
```

GitHub Actions runs the portable checks. Tests mock hosted responses; verification
does not make paid model calls. No new model evaluation is implied by a code test.

## Files and evidence

| Path | Purpose |
|---|---|
| `configs/bugs.json` | One selected evaluation list |
| `configs/local.json`, `configs/hosted.example.json` | Local and hosted model settings |
| `src/debuglab/cli.py`, `cohort_run.py`, `study.py` | Commands, frozen runs, workers and deadlines |
| `src/debuglab/pipeline.py` | Per-bug source preparation, localization and four repairs |
| `src/debuglab/model.py`, `interface.py` | Model calls, token accounting and strict method mappings |
| `src/debuglab/source.py`, `retrieval.py`, `spectrum.py` | Java indexing, context selection and Ochiai |
| `src/debuglab/edits.py`, `harness.py` | Isolated patch application and native validation |
| `java/SpectrumRunner.java` | JUnit/JaCoCo per-test coverage helper |
| `src/debuglab_gold/` | Separate ground-truth evaluation, reporting and comparison utilities |
| `tests/` | Portable and native regression tests |
| `results/final/` | Preserved measured results for the report |
| `outputs/<run>/` | Ignored run manifest, snapshots, reports, worker logs and archives |
| `outputs/<run>/runs/<bug>/` | Context, model calls, canonical edits, test logs and outcomes |
| `work/` | Ignored local tools, gold evaluation data and disposable checkouts |

Raw prompts and responses, original-numbered edits, test proofs and hashes remain
available locally for analysis. Ground-truth fixes are isolated in the evaluator,
never supplied to retrieval or generation. Optional historical interface helpers
remain for reading/testing saved evidence; new configurations cannot enable their
experimental alternatives. Private reports, slides, credentials and runtime data
are not distributed in Git.
