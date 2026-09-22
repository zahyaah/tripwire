# Implementation Plan: TripWire

## Overview

TripWire is an evaluation and observability harness for multi-step agents, built around an Inbox
Triage Agent running on synthetic data. The build order is a walking skeleton first — one golden
case running end to end through trace recording, assertions, and a report — then breadth (more
cases, the judge, calibration), then the CI gate that turns all of it into a build verdict.

Specs: `CAPABILITY-MAP.md` (module ids and dependency direction), `SPEC.md` (shared foundations).
Module specs `SPEC-<module-id>.md` are written in build order after the map is approved.

Tasks are tracked in `tasks/todo.md`.

## Architecture Decisions

- **Manual agentic loop, not the SDK tool runner.** The harness must own every step boundary to
  record spans, enforce step budgets, and attribute cost per step. The tool runner hides the loop,
  which is exactly the thing being measured.
- **The harness never imports the agent, and the agent never imports the harness's scoring.**
  `trace-core` is generic over runs. `assertions` and `judge` read recorded traces, not live
  objects. This is what makes "point it at another agent" a config change rather than a rewrite.
- **Record/replay at the model-call boundary, keyed by a hash of the full request.** CI runs with
  no API key and no network, deterministically, while keeping real recorded token counts, real
  recorded latency, and therefore real cost math. A cassette miss in replay mode is a hard
  failure that prints the missing key, not a silent live call.
- **Deterministic assertions and the judge are siblings, not a fallback chain.** Routing, ordering,
  argument correctness, and budgets are decided by code. The judge only scores what code cannot
  express (helpfulness, tone, unsupported claims). This keeps the build gate mostly deterministic.
- **The judge is calibrated, and its agreement is reported with every score.** Human labels are
  split into dev (for tuning the judge prompt) and holdout (for the reported number). A dimension
  below kappa 0.6 on holdout is published as low-confidence rather than treated as truth.
- **Statistics are hand-written and unit-tested against hand-computed values.** Cohen's kappa,
  quadratic-weighted kappa, and the bootstrap CI are ~60 lines total. Owning them removes a heavy
  dependency and makes the calibration claim defensible in a code review.
- **Traces are JSONL plus a self-contained HTML file.** No collector to run, works as a CI
  artifact, opens offline. An OpenTelemetry exporter is a later adapter, not a v1 requirement.
- **Cost from reported usage against a versioned price table.** Cache read and cache write tokens
  are priced separately; money is integer micro-dollars or `Decimal`, never accumulated floats.

## Dependency Graph

```
trace-core (schemas, JSONL store, cost, llm gateway + cassettes)
    │
    ├── synthetic-data (corpus generator, golden set format)
    │       │
    │       ├── agent (tools, prompt, instrumented loop)
    │       │       │
    │       │       ├── assertions (matchers, runner, pytest suite)
    │       │       │       │
    │       │       │       ├── report (HTML viewer, run summary)
    │       │       │       │       │
    │       │       │       │       └── ci (baselines, gate, workflow)
    │       │       │       │
    │       │       └── judge (rubric, labels, calibration) ──┘
```

Build order: `trace-core`, `synthetic-data` → `agent` → `assertions` → `judge` → `report` → `ci`

## Task List

### Phase 1: Foundation (walking skeleton)
- [ ] Task 1: Repository scaffold with uv, ruff, mypy, pytest
- [ ] Task 2: Trace schemas and JSONL trace store (`trace-core`)
- [ ] Task 3: Price table and usage rollup (`trace-core`)
- [ ] Task 4: Instrumented Anthropic gateway with cassette record/replay (`trace-core`)

### Checkpoint: Foundation
- [ ] `uv run pytest -q` green, `uv run mypy src` clean
- [ ] A recorded model call replays byte-identically with its usage and latency intact
- [ ] Cost for a replayed call matches a hand-computed value

### Phase 2: Agent and first end-to-end case
- [ ] Task 5: Synthetic corpus generator (`synthetic-data`)
- [ ] Task 6: Inbox Triage Agent tool schemas and implementations (`agent`)
- [ ] Task 7: Instrumented agent loop (`agent`)
- [ ] Task 8: Golden set format, loader, and 8 seed cases (`synthetic-data`)
- [ ] Task 9: Assertion matchers (`assertions`)
- [ ] Task 10: Assertion runner and parametrized regression suite (`assertions`)

### Checkpoint: End-to-end
- [ ] `uv run tripwire run --suite data/golden --mode replay` passes all 8 seed cases
- [ ] Deliberately breaking one expected tool argument fails with a message naming case and step
- [ ] Every run writes a readable `runs/<run_id>/trace.jsonl`
- [ ] Review with human before scaling the golden set

### Phase 3: Breadth and the judge
- [ ] Task 11: Expand the golden set to 60 cases and record cassettes (`synthetic-data`)
- [ ] Task 12: Judge rubric and structured-output judge call (`judge`)
- [ ] Task 13: Labeling CLI and label store (`judge`)
- [ ] Task 14: Calibration statistics and agreement report (`judge`)

### Checkpoint: Measurement
- [ ] Routing accuracy reported overall, per intent, per case
- [ ] 120 human labels committed with a dev/holdout split manifest
- [ ] Holdout agreement reported per dimension with a bootstrap 95% CI
- [ ] Low-kappa dimensions are marked low-confidence in the summary, not hidden

### Phase 4: Observability and the gate
- [ ] Task 15: Self-contained HTML trace viewer (`report`)
- [ ] Task 16: Run summary in Markdown and JSON (`report`)
- [ ] Task 17: Baselines and the regression gate (`ci`)
- [ ] Task 18: GitHub Actions workflow with artifacts and a PR comment (`ci`)
- [ ] Task 19: Broken-prompt demo proving the gate fails a routing regression (`ci`)
- [ ] Task 20: README and docs with measured results (`report`)

### Checkpoint: Complete
- [ ] A pull request that changes the agent prompt fails CI on the routing regression
- [ ] The failing run's HTML trace downloads from the CI artifacts and opens offline
- [ ] README numbers are all traceable to a committed run summary
- [ ] All `SPEC.md` success criteria met

## Parallelization

- Safe in parallel after Task 4: Task 5 (`synthetic-data`) and Task 6 (`agent` tools) — the corpus
  contract is the only shared surface, and Task 5 defines it first.
- Safe in parallel after Task 10: Task 12-14 (`judge`) and Task 15-16 (`report`), since they read
  the same trace records and do not share code.
- Must be sequential: Tasks 1-4 (everything reads the trace schema), Task 11 before any judge
  calibration (labels need runs to label), Task 17 before Task 18.
- Needs coordination: Task 2's span schema is the contract for Tasks 9, 12, and 15. Freeze it at
  the Foundation checkpoint; changing it later invalidates committed traces.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Cassette drift: prompt edits invalidate every cassette, making the gate unrunnable | High | Hash only the semantically relevant request fields; keep the system prompt in one file; provide `tripwire run --mode record --only <case>` for targeted re-recording; treat bulk re-record as an "ask first" action |
| Synthetic data too easy: the agent scores 100% and the harness proves nothing | High | Reserve 15 of 60 cases as adversarial (urgent-looking spam, angry FAQ, must-refuse); require at least one committed case the current agent fails, documented as a known gap |
| Judge agreement comes out low and the LLM-as-judge story looks weak | Medium | Low agreement is a finding, not a failure — report it, mark the dimension low-confidence, and write up which dimensions humans and the judge genuinely disagree on |
| Judge prompt overfit to labels | Medium | Dev/holdout split fixed before tuning; holdout kappa is the only reported number; tuning against holdout is a "never" in `SPEC.md` |
| Replay hides nondeterminism, so the agent looks more stable than it is | Medium | A weekly or on-demand `--mode live` run over the golden set, its variance recorded in the run summary as a separate figure from the CI number |
| Hand-labeling 120 runs stalls the project | Medium | Labeling CLI with keyboard-only flow (Task 13) before labeling starts; label in two sittings of 60; the harness ships useful without the judge |
| Tool-argument matching too strict, producing false failures on trivial wording changes | Medium | Three matcher strengths (exact, subset, regex) with subset as the default for free-text arguments; required vs advisory assertions |
| Cost math wrong, so every cost figure is wrong | High | Unit tests against hand-computed values including cache read and cache write tokens; price table versioned with an `as_of` date |
| Scope creep into OpenTelemetry, a dashboard, or a second agent | Medium | Named non-goals in `SPEC.md`; any addition requires a spec update first |

## Open Questions

Tracked in `SPEC.md` § Open Questions: adversarial case mix (assumed 15 of 60), judge rubric
dimensions, whether judge regressions block the build or only report, and whether v1 includes a
second agent. All four have working assumptions; none block Phase 1 or Phase 2.
