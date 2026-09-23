# TripWire

An evaluation and observability harness for multi-step agents, built against a synthetic Inbox
Triage Agent. TripWire answers one question for a reviewer: **did this change to the agent's
prompt, tools, or model make routing behavior worse — before merging, without running anything
locally?**

It gives a golden set of expected agent behavior, per-step assertions (right tool, right
arguments, right order, nothing forbidden), an LLM-as-judge scored against calibrated human
labels (never trusted blind), per-step cost/token/latency tracking, a self-contained HTML trace
viewer, and a regression gate wired into CI.

## Status

Every piece described below is implemented and tested. What's genuinely still open: the Gemini
free tier's 20-request/day cap means the 60-case golden set doesn't have committed cassettes yet,
so the regression suite, a real calibration report, and a live confirmation of the broken-prompt
demo are pending real API access, not unbuilt. See `docs/known-gaps.md` for the exact, current
state of every blocked piece — nothing here is a fabricated result.

## Quick start

```
uv sync
uv run pytest -q -m "not regression"    # fast gate: unit + tool-level tests, no cassettes needed
uv run mypy src                         # strict on src/tripwire
uv run ruff check .
```

To run the agent against the golden set yourself:

```
uv run tripwire gen-corpus --seed 1337 --threads 400   # deterministic synthetic inbox
uv run tripwire run --suite data/golden --mode replay  # needs committed cassettes (see Status)
uv run tripwire run --suite data/golden --mode record  # calls the live API, needs GEMINI_API_KEY
```

## Commands

| Command | Description |
|---|---|
| `tripwire gen-corpus --seed <n> --threads <n>` | Generate the deterministic synthetic inbox |
| `tripwire run --suite data/golden --mode <live\|record\|replay>` | Run the golden set against the agent |
| `tripwire run --suite data/golden --prompt-variant <name>` | Run against a committed prompt variant (`agents/inbox_triage/prompt_variants/`) |
| `tripwire report --run <run_id\|latest> --open` | Regenerate a run's HTML trace (and a suite's `summary.md`) from what's already recorded |
| `tripwire label --run <run_id>` | Interactively label a recorded run against the judge rubric |
| `tripwire calibrate --labels data/labels/human.jsonl` | Score labeled runs with the judge, report agreement vs. human labels |
| `tripwire gate --run latest --baseline baselines/main.json` | Compare a run's summary to the committed baseline; exits non-zero on regression |
| `tripwire gate --run latest --update` | Explicitly overwrite the baseline (never automatic) |

Every command's `--mode` defaults to `$TRIPWIRE_LLM_MODE`, else `replay`. `live`/`record` need
`GEMINI_API_KEY` set; `replay` never touches the network and never needs one.

## Architecture

Corpus → golden set → agent loop → append-only trace (`runs/<run_id>/trace.jsonl`) → assertions +
judge + cost rollup, all reading only the trace → HTML viewer, suite summary, and the regression
gate. The full walkthrough, including why the trace is the *only* interface between every stage,
lives in **`docs/architecture.md`**.

Significant decisions and why they were made the way they were — the model provider (and its two
earlier, abandoned choices), the record/replay cassette design, hand-written calibration
statistics instead of scikit-learn, and the harness/agent import boundary — are recorded as ADRs
in **`docs/decisions/`**.

## Results

**Not yet available.** SPEC.md's own rule: "if a number was not measured in a recorded run, it
does not go in a report or a README." No golden-set cassette recording has completed yet (see
Status above), so there is no real run to source a routing-accuracy, judge-agreement, cost, or
latency figure from. `baselines/main.json` is currently a genuine 0-case placeholder, built
through the same `build_summary` code a real suite run would use, not hand-typed.

Once a full suite run completes:

```
uv run tripwire run --suite data/golden --mode replay
```

this section will hold the real routing accuracy (overall and per intent), judge scores with
their holdout agreement, cost per case, and p95 step latency — copied from that run's
`runs/<suite_run_id>/summary.md`, with the run id kept alongside so every figure traces back to a
committed file.

## Docs

- `docs/architecture.md` — how the pieces fit together
- `docs/decisions/` — ADRs for the significant, expensive-to-reverse calls
- `docs/known-gaps.md` — exactly what's blocked, why, and what's predicted vs. measured
- `docs/judge-calibration.md` — the judge-vs-human agreement report (pending real labels)
- `docs/demo.md` — the broken-prompt-fails-the-gate demonstration

## Testing

```
uv run pytest -q -m "not regression"   # fast: unit + tool-level, no network, no cassettes
uv run pytest -q -m regression         # the golden-set suite, replay mode, needs committed cassettes
uv run pytest -q -k broken_prompt      # the regression-gate mechanism demo, self-contained
uv run pytest --cov=src/tripwire --cov-report=term-missing
```

No test calls a live API. Live calls happen only through `tripwire run --mode record|live`,
driven by a human, never from CI (`.github/workflows/eval.yml` asserts no API key is present).

## Project structure

```
src/tripwire/
  core/          Run and span schemas, run ids, JSONL trace store
  cost/          Versioned price table, usage rollup, p95 latency
  llm/           OpenAI-compatible gateway, cassette record/replay
  assertions/    Matchers and the assertion runner
  judge/         Rubric, judge client, label store, calibration stats
  labeling/      Interactive labeling CLI
  report/        HTML trace viewer, run summary, the regression gate
  cli.py         typer entry point
agents/
  inbox_triage/  The agent under test: prompt, tool schemas, tool impls, loop
data/            Synthetic corpus, golden set YAML, human labels
fixtures/cassettes/   Recorded model calls, keyed by request hash (committed)
tests/
  unit/          Fast, no network, no cassettes needed
  regression/    Golden set suite (replay mode) and the broken-prompt demo
runs/            Trace output, gitignored
baselines/       Committed baseline metrics for the regression gate
docs/            Architecture, ADRs, calibration write-up, known gaps, demo
.github/workflows/eval.yml
```
