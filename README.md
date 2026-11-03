# DebugLab

A reproducible, one-shot study of fault localization and Java program repair on
Defects4J. DebugLab compares Ochiai spectrum-based localization, LLM-only
localization and their combination, then evaluates one patch under each of four
repair conditions. Models receive the same frozen source and no test feedback.

## Final report and data

- [Final research report](results/study-100/report.md), with figures and references.
- [Results, evidence guide and limitations](results/study-100/README.md).
- [Machine-readable summary](results/study-100/summary.json).
- [Editable Google Doc](https://docs.google.com/document/d/1prFTs354ZOPsbP989uwf3ajJfEMOTJnEU7c4Mo4OceE).

The final study compares the weaker **Qwen2.5-Coder 7B** with the stronger
**GPT-5.4 mini** on the same 100 selected bugs, with 600 calls and 400 repair
attempts per model. Review classifies 30 Qwen and 124 GPT repairs as valid, versus
43 and 160 passing the original test suites. Passing tests alone is not correctness.

The earlier [60-bug midpoint evaluation](results/final/README.md) is retained as
historical evidence. It uses a different cohort/protocol and is not pooled with the
final study. Experimental agentic prototypes and preparation drafts are excluded
from this release; iterative repair remains future work in the paper.

## Verify the published numbers, without models or Java

Python 3.11+ is sufficient. This command uses only the standard library and makes
no network requests:

```sh
python3 scripts/verify-study-data.py
```

It checks evidence hashes, all selected denominators, paired input hashes,
localization Top-1/3/5, all repair statuses, review assignments, full-suite proof
summaries and the final paper totals. It does not rerun native tests or establish
correctness for every possible input. See the [evidence guide](results/study-100/README.md).

## Install the harness

Use Python 3.11 and a Defects4J-supported Java installation (the measured native
setup uses Java 11), plus Git, Subversion, Perl and `cpanm`. Model weights and
benchmark tools are separate downloads.

```sh
git clone https://github.com/AanshDevpura/debuglab.git
cd debuglab
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-lock.txt
pip install --no-deps -e .
mkdir -p work/tools
git clone https://github.com/rjust/defects4j.git work/tools/defects4j
(cd work/tools/defects4j && cpanm --installdeps . && ./init.sh)
```

Use Defects4J **3.0.1** to match the study; follow its
[setup instructions](https://github.com/rjust/defects4j#setting-up-defects4j) and
set `JAVA_HOME`/`PATH`. The default executable is
`work/tools/defects4j/framework/bin/defects4j`.

```sh
mkdir -p work/tools/jacoco
curl --fail --location https://repo.maven.apache.org/maven2/org/jacoco/org.jacoco.agent/0.8.12/org.jacoco.agent-0.8.12-runtime.jar --output work/tools/jacoco/agent.jar
curl --fail --location https://repo.maven.apache.org/maven2/org/jacoco/org.jacoco.cli/0.8.12/org.jacoco.cli-0.8.12-nodeps.jar --output work/tools/jacoco/cli.jar
source scripts/env.sh
java -version
defects4j info -p Lang
```

`scripts/env.sh` discovers optional workspace tools and fixes the benchmark timezone
to `America/Los_Angeles`. This is an experiment setting, not the user's timezone.

## Run a new evaluation

### Qwen2.5-Coder 7B

Install/start [Ollama](https://docs.ollama.com/quickstart), then:

```sh
ollama pull qwen2.5-coder:7b
ollama create debuglab-qwen-coder-32k -f configs/ollama-7b.Modelfile
source scripts/env.sh
debuglab run --config configs/study-qwen.json --output outputs/qwen-run-1
```

For a smaller native check, select one bug in a fresh output directory:

```sh
debuglab run --config configs/study-qwen.json --bug Time-23 --output outputs/qwen-smoke
```

The checked-in study configs enable independent statement rankings, compact
same-class helper context and strict original-method replacements. Qwen uses a
32,768-token context, 4,096-token output cap, temperature 0 and seed 20261009.
The model tag can change upstream; the published data records the measured setup.

### GPT-5.4 mini

Use the pinned `gpt-5.4-mini-2026-03-17` model and a funded API project. Set the
key in your environment, never in JSON or Git. To avoid shell history:

```sh
read -s OPENAI_API_KEY
export OPENAI_API_KEY
source scripts/env.sh
python scripts/run-hosted.py --output outputs/gpt-run-1
```

The wrapper runs the same one-shot coordinator with the study's **$60 reservation
cap**, at most 600 dispatches, unique call paths, no retries and preserved transport
receipts. Reservations use conservative input bytes and the maximum output allowance;
they are not refunded. The cap covers this run, not account-wide spending or taxes.
It uses the study pricing assumptions ($0.75/M input and $4.50/M output tokens),
which must be checked before a future paid reproduction. Inspect `TRANSPORT_STOP.json` and
receipts after an API/budget halt; the wrapper refuses automatic continuation.
Use the wrapper rather than calling `debuglab run` directly for the paid model.

The API uses temperature 0, no seed, up to 4,096 output tokens and strict structured
output. Its larger context window is used for conservative byte accounting; the
study did **not** supply extra source to GPT. Provider schema encoding omits
unsupported uniqueness keywords, but the parser still rejects duplicate rankings.

### Resume, reports and paired inputs

Use the exact same command/config to resume an interrupted native evaluation.
Do not change source, model settings or selection within a run. Saved responses and
`IN_FLIGHT` requests are never regenerated. API halts require diagnosis, not a retry
or a fresh sample to replace a disappointing answer.

```sh
debuglab report --output outputs/qwen-run-1
```

This regenerates sealed reports from saved artifacts, without model calls.
Reports contain eligible and unranked counts, Top-1/3/5, context-conditional metrics
and all repair statuses. Gold evaluation runs separately from generation.

Fresh executions are not guaranteed to retrieve byte-identical source. For the
paper, original debugging artifacts were copied before inference and their hashes
matched across both models. To reproduce that control with new runs, prepare the
second run using `debuglab.replay.reuse` on the first run's completed `runs/` folders
before dispatch. For example, after completing `outputs/qwen-run-1`:

```sh
python - <<'PYCODE'
from pathlib import Path
from debuglab.cohort_run import prepare_run
from debuglab.replay import reuse
from debuglab.selection import read_bugs

cfg, saved = prepare_run(Path("configs/study-gpt54-mini.json"),
                         output=Path("outputs/gpt-paired-1"))
for bug in read_bugs(Path(cfg.bugs_path)):
    reuse(cfg, bug, Path("outputs/qwen-run-1/runs"))
PYCODE
python scripts/run-hosted.py --output outputs/gpt-paired-1
```

This copies original buggy inputs/context, not answers, gold or outcomes. Preserve equal contexts and compare their hashes; two independent fresh
retrieval runs alone do not establish this control.

The 100-bug list is reference-and-retrieval-conditioned: all known faulty methods
fit the supplied source and the known fixes fit at most three original methods.
This is not an unbiased Defects4J sample or guaranteed model success. The offline
`debuglab_gold.interface_readiness` module certifies reference expressibility;
`python -m debuglab_gold.interface_readiness --help` documents its required inputs.

## Protocol

1. Reproduce original failing tests, collect per-test coverage and compute Ochiai.
2. Retrieve complete buggy methods and compact declarations from failure text,
   stack traces, lexical relevance and an unranked executed-method set, without gold.
   At most two unambiguous same-class helpers follow an accepted caller within budget.
3. Freeze source once per bug. Make two independent localization calls, LLM-only and
   combined. Ochiai is a separate ranker that needs no model call.
4. Make four separate repair calls: direct, spectrum-guided, LLM-guided and
   combined-guided. Every condition gets the same source; only hints differ.
5. Parse the first answer, enforce at most three complete original-API method or
   constructor replacements, then check Java syntax, compile, run trigger tests
   and run the full suite on isolated original checkouts. No revisions or feedback.

Added helpers, fields and classes are outside this protocol. Consistent parameter
binding renames are allowed. Native plausibility requires `defects4j test` without
`-t`, exit 0, no timeout and a final `Failing tests: 0`. Every format, syntax,
compilation, behavioral and timeout failure stays in the selected denominator.
Reference-based review is a separate post-run assessment, not model feedback.

## Development checks

```sh
ruff check .
ruff format --check .
pytest -q -m 'not integration'
python scripts/check-hosted-guard.py
python scripts/verify-study-data.py
scripts/check-release

# After installing Java and Defects4J; no model calls:
source scripts/env.sh
scripts/verify
```

CI runs portable tests, transport-guard tests, evidence verification and the release
boundary check. Large runtime checkouts, raw request/response archives, credentials,
paper scratch files and models stay under ignored local paths. The export command
and publication scope are documented in the results guide.
