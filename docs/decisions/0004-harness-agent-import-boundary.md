# ADR-0004: The agent under test never imports the measuring instrument

## Status
Accepted

## Date
2026-09-23

## Context
TripWire measures an agent (`agents/inbox_triage/`) using assertions and a judge
(`src/tripwire/assertions/`, `src/tripwire/judge/`). If the agent could import anything from
those modules, it could — accidentally or otherwise — see how it's being scored: a golden case's
expected tool calls, a matcher's pass/fail logic, the judge's rubric weighting. That would make
every reported number worthless, the same failure class as a student seeing the answer key.

Separately, `CAPABILITY-MAP.md` states a second, related goal: keep `trace-core`
(`src/tripwire/core/`) reusable for evaluating a second agent later, which means it must not
encode anything specific to inbox triage.

## Decision
Two distinct rules, enforced at different strictness:

1. **`agents/` never imports from `src/tripwire/assertions` or `src/tripwire/judge`.** Enforced
   by an import-boundary test (`tests/unit/test_boundaries.py`, tasks/todo.md Task 6) that fails
   the build if this is ever violated. No exception.
2. **`src/tripwire/core` never imports `agents`.** The agent depends on the trace schema for
   instrumentation; the trace schema never depends back on the agent — a one-way edge, matching
   `CAPABILITY-MAP.md`'s stated build order (`trace-core` before `agent`).

`src/tripwire/assertions` itself splits in two: `matchers.py` and `results.py` (the actual scoring
logic) hold to rule 2's shape exactly — they evaluate a recorded `list[Span]` and never import
`agents` at all, so they could score a second agent's trace unmodified. `runner.py` is the one
documented exception: it is the orchestrator that actually executes the agent (via
`agents.inbox_triage.loop.run_agent`) to produce the trace the matchers then read — something has
to call the thing being measured. `CAPABILITY-MAP.md` records this exception explicitly, with a
correction note: an earlier draft of that map stated the no-import rule too broadly, before
Task 10 showed it couldn't hold for the runner specifically.

## Alternatives Considered

### One shared module, no import boundary
- Pros: simpler — no need to reason about which file can import what.
- Cons: the agent would have direct code-path access to its own scoring logic, undermining the
  entire premise that a routing failure represents genuine model behavior rather than a lucky
  read of the matcher's expectations.
- Rejected outright — this is the risk the rule exists to prevent.

### Enforce the boundary by convention/code review only, no automated test
- Pros: less test-maintenance surface.
- Cons: a convention with no automated check drifts silently — exactly the failure this project's
  own `SPEC.md` was caught by once already (the NVIDIA-to-Gemini provider swap left `SPEC.md`
  itself stale for a stretch; see ADR-0001). A boundary this load-bearing needs a test, not a
  memory.
- Rejected: `tests/unit/test_boundaries.py` exists specifically so this can't drift unnoticed.

### Apply rule 2 (no import back into `agents`) to every module in `src/tripwire`, no exceptions
- Pros: a single, simple, universally-stated rule.
- Cons: `assertions/runner.py` and `cli.py`'s `run` command must execute the agent to produce
  anything to measure at all — a blanket rule here is unsatisfiable, not merely inconvenient.
- Rejected in favor of the narrower rule 1 (only `assertions`/`judge`'s *scoring logic* is
  off-limits to the agent) plus the one documented, tested exception for the orchestrator.

## Consequences
- A second agent could reuse `matchers.py`/`results.py`, `trace-core`, and `report` entirely
  unmodified — only a new `runner.py`-equivalent orchestrator and a new golden set would be
  agent-specific work (this is explicitly future work, not built in v1: `CAPABILITY-MAP.md`).
- Every PR that touches `agents/inbox_triage/` is implicitly checked against rule 1 by
  `test_boundaries.py` running in the normal test suite — no separate lint step needed.
- The one exception (`runner.py`) is a permanent, load-bearing asymmetry: it is trusted with
  knowledge of golden-set expectations that `agents/` itself must never see, so any future
  refactor that moves orchestration logic into a new file must carry this same restriction with
  it, not assume it only applies to the current file name.
