# Capability Map: TripWire

An evaluation and observability harness for multi-step agents. The system under test is an
Inbox Triage Agent rebuilt on synthetic data; TripWire is the harness around it.

This initiative bundles several independently testable capabilities, so it is decomposed into
modules before any module spec is written. Module ids are stable and are how specs, plans, and
tasks select work.

| Module id | Responsibility | Depends on |
|---|---|---|
| `trace-core` | Run/span/step record schema, run ids, JSONL trace store, per-step token, cost, and latency accounting, model-call record/replay gateway | — |
| `synthetic-data` | Deterministic synthetic inbox corpus (threads, customers, orders) and the golden set format: inputs plus expected tool calls, arguments, order, and outcomes | — |
| `agent` | The Inbox Triage Agent under test: tool definitions, tool implementations over the corpus, system prompt, instrumented agent loop | `trace-core`, `synthetic-data` |
| `assertions` | Per-step assertion matchers (tool called, arguments, order, forbidden calls, call counts, final outcome, step/cost budgets) and the assertion runner | `trace-core`, `synthetic-data`, `agent` |
| `judge` | LLM-as-judge scoring against a rubric, human label store and labeling CLI, calibration statistics (agreement, Cohen's kappa, quadratic-weighted kappa, bootstrap CI) with a dev/holdout split | `trace-core`, `synthetic-data` |
| `report` | Self-contained HTML trace viewer (waterfall timeline, step drill-down, assertion overlay, cost column) and machine-readable run summaries | `trace-core`, `assertions`, `judge` |
| `ci` | pytest regression suite, baselines, regression gate thresholds, GitHub Actions workflow, PR summary comment | `assertions`, `judge`, `report` |

Build order: `trace-core`, `synthetic-data` → `agent` → `assertions` → `judge` → `report` → `ci`

Dependency notes:

- `agent` depends on `trace-core` for instrumentation; `trace-core` never imports `agent`. The
  agent is one consumer of a generic harness, which is what makes the harness reusable for a
  second agent later.
- `assertions` reads traces produced by `trace-core` and expectations produced by
  `synthetic-data`. Its scoring primitives (`matchers.py`, `results.py`) never import `agent` and
  assert over recorded spans only, never a live agent object — that's what lets them run
  unmodified against a second agent's trace later. The assertion *runner* (`runner.py`) is the
  one exception, by necessity: it is the orchestrator that executes the agent (via
  `agent`'s `run_agent`) and hands the resulting trace to the matchers — something has to call
  the thing being measured. It depends on `agent` today because there is only one agent to run;
  making the runner agent-pluggable is future work, not a Task 10 requirement. (Corrected from
  this map's original Phase 0 draft, which stated the no-`agent`-import rule too broadly — Task
  9's matchers keep it exactly; Task 10's runner could not.)
- `judge` and `assertions` are siblings, not a chain: deterministic assertions cover routing,
  the judge covers quality that assertions cannot express. Either can ship without the other.
- `ci` is the only module that depends on all of the scoring modules, because the regression
  gate is the thing that combines them into a build verdict.

Each module gets its own spec (`SPEC-<module-id>.md`) written in build order, after this map is
approved. `SPEC.md` at the project root holds the foundations shared by every module: stack,
commands, structure, style, testing strategy, and boundaries.
