Written for: an engineer or agent picking up this codebase who needs the shape of the system
before the first line of code, not after.

# Architecture

TripWire evaluates an agent by running it, recording everything it did as a flat, typed trace,
and then checking that trace three different ways: deterministic assertions, an LLM judge, and a
cost/latency rollup. Every later stage reads only the trace — never a live agent object — which
is what lets the same scoring code work against a run from five seconds ago or a run from a
recorded cassette three weeks old.

## The pipeline, start to finish

```
data/corpus/            deterministic synthetic inbox (threads, customers, orders)
        |
        v
data/golden/*.yaml       one case per file: input thread + expected tool calls/outcome
        |
        v
agents/inbox_triage/     the agent under test: tools, system prompt, the manual loop
        |  (via src/tripwire/llm/gateway.py -- every model call becomes a span)
        v
runs/<run_id>/trace.jsonl   append-only, one JSON object per span, crash-safe
        |
        +--> src/tripwire/assertions/   deterministic pass/fail per golden case
        +--> src/tripwire/judge/        LLM-as-judge score, calibrated against human labels
        +--> src/tripwire/cost/rollup.py   tokens, cost, p95 latency, from spans only
        |
        v
src/tripwire/report/      HTML trace viewer, suite summary (JSON + Markdown), the gate
        |
        v
.github/workflows/eval.yml   runs it all in CI, replay mode, no API key, posts the PR comment
```

## Record/replay: how CI runs with no API key

Every model call goes through `ModelGateway` (`src/tripwire/llm/gateway.py`), which can operate
in `live` (call the API), `record` (call the API, save a cassette), or `replay` (cassette only,
no network) mode. A cassette is keyed by a stable hash of the semantically relevant request
fields, and stores the real recorded `usage` and `latency_ms` — so a replayed run's cost and
latency numbers are the real ones from when it was recorded, not synthetic placeholders. CI
always runs in `replay` mode and explicitly asserts no API key is present in its environment, so
a step that somehow needed the network fails loudly instead of silently going live.

Full rationale and the alternatives considered: **ADR-0002**.

## The harness/agent boundary

The agent under test (`agents/inbox_triage/`) is not allowed to import anything from
`src/tripwire/assertions` or `src/tripwire/judge` — the thing being measured must never see the
measuring instrument. This is enforced by an automated test
(`tests/unit/test_boundaries.py`), not just a convention.

The inverse direction is looser by necessity: something has to actually run the agent to produce
a trace to score. `src/tripwire/assertions/runner.py` is the one file that imports
`agents.inbox_triage.loop.run_agent` — everything else in `assertions` and all of `judge` reads
only recorded `Span` objects and would work unmodified against a second agent's trace.

Full rationale, including why this exact split (not a blanket no-import rule) was necessary:
**ADR-0004**.

## Why spans are the only interface

`src/tripwire/core/records.py` defines a discriminated union of four span kinds
(`agent_run`, `model_call`, `tool_call`, `judge_call`), each carrying a `span_id`,
`parent_span_id`, `step_index`, `latency_ms`, and `is_error`. `TraceWriter` appends them to
`runs/<run_id>/trace.jsonl` as they happen — a process that crashes mid-run still leaves a
readable prefix, and `TraceReader` tolerates a truncated final line rather than refusing to load
anything.

Everything downstream — matchers, the judge, cost rollup, the HTML viewer, the suite summary, the
regression gate — is a pure function over `(RunRecord, list[Span])`. None of them touch a live
agent, a live model client, or each other's internals. This is why `report/gate.py`'s tests use
entirely synthetic `RunSummary` objects and still exercise the real regression-detection logic:
the interface between "a run happened" and "here's whether it passed" is just data.

## Judge calibration, not judge trust

The LLM-as-judge (`src/tripwire/judge/`) scores a run's transcript against a four-dimension
rubric via structured output — no prefill, no prose parsing (`response_format` JSON-schema mode,
validated with `RubricScore.model_validate_json`). Its score is never taken as ground truth: a
separate human-labeling CLI (`tripwire label`) collects human scores on the same rubric, split
into a `dev`/`holdout` set by a deterministic hash of `(run_id, seed)` that's recorded once and
never silently recomputed. `tripwire calibrate` then reports, per dimension, raw agreement,
Cohen's kappa or quadratic-weighted kappa, and a bootstrap 95% CI — computed by hand, not
imported, so the reviewer can read the ~150 lines that produce the number (**ADR-0003**). A
dimension whose holdout kappa lands below 0.6 is published as low-confidence, never silently
treated as a real score, and the regression gate is wired to never block on a dimension it hasn't
earned trust on (`src/tripwire/report/gate.py`).

## The regression gate

`tripwire gate` compares a suite's `RunSummary` against a committed baseline
(`baselines/main.json`) and fails on four independent conditions: any required assertion failure
in the current run, a routing-accuracy drop beyond tolerance, a cost or p95-latency regression
beyond tolerance, or a judge-dimension kappa drop on a dimension the baseline already trusted.
Updating the baseline (`tripwire gate --update`) is a separate, explicit action — evaluating the
gate never writes to it. See `docs/demo.md` for this mechanism proven against a deliberately
broken prompt variant.

## What's genuinely still open

`docs/known-gaps.md` tracks the current blocker in detail: the Gemini free tier's 20-request/day
cap means the 60-case golden set doesn't have committed cassettes yet, so the regression suite,
the real calibration report, and a live confirmation of the broken-prompt demo are all pending —
not unbuilt, blocked on quota. Every piece of infrastructure described above is implemented and
tested against scripted/synthetic data in the meantime; nothing here is a stub.
