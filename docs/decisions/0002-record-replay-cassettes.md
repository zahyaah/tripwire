# ADR-0002: Record/replay cassettes for deterministic, API-key-free CI

## Status
Accepted

## Date
2026-09-23

## Context
SPEC.md's whole premise is that a PR can fail CI on a routing regression "without running
anything locally," and that CI needs no API key and costs nothing per build (Assumption 2). That
requires every model call the regression suite makes to be reproducible offline: same request,
same response, every time, on a machine with no network access and no secret.

At the same time, the harness needs real behavior to test against during development — a golden
case's expectations are only meaningful if they were checked against how the model actually
responds, not an author's guess.

## Decision
`ModelGateway` (`src/tripwire/llm/gateway.py`) runs in one of three modes:

- `live` — calls the real API, records nothing.
- `record` — calls the real API and writes a cassette (`fixtures/cassettes/<key>.json`) for every
  call.
- `replay` — never touches the network; loads the cassette matching the request's key, or raises
  `CassetteMissError` naming the key and the re-record command.

The cassette key is a stable SHA-256 hash over the semantically relevant request fields (model,
messages, tools, tool_choice, response_format, `max_tokens`), canonicalized so dict key order
never changes the key (`compute_cassette_key`, `src/tripwire/llm/cassettes.py`). `max_tokens` is
included deliberately: a smaller value can truncate a completion and flip `finish_reason` to
`"length"` — a different response to a different question, so two requests differing only in
`max_tokens` must not collide on one cassette.

A cassette replays the recorded `usage` and the recorded `latency_ms`, not a fixed or zeroed
value — cost and latency metrics stay real under replay, not synthetic.

CI (`.github/workflows/eval.yml`) sets `TRIPWIRE_LLM_MODE=replay` and asserts no API key is
present in the environment, so a replay-mode call that somehow needed the network would fail
loudly rather than silently degrading to a live call.

## Alternatives Considered

### Mock the SDK client directly in every test
- Pros: no cassette file format to design or maintain.
- Cons: every test author writes their own fake response by hand — nothing forces it to resemble
  what the real API actually returns, and nothing catches drift between the fake and reality over
  time.
- Rejected for the regression suite specifically (still used deliberately for harness-level unit
  tests like `tests/unit/test_loop.py` and `tests/regression/test_broken_prompt.py`, where the
  scripted response's exact shape is the point being tested, not the model's real behavior).

### Record once, hand-edit cassettes to cover more cases
- Pros: fewer real API calls needed.
- Cons: a hand-edited cassette is not something the model actually said — using it in the
  regression suite would silently violate "never fabricate a metric" (SPEC.md § Boundaries): a
  case would be asserted as passing (or failing) against content that was authored, not observed.
- Rejected outright; the one place a scripted/non-live response is used
  (`tests/regression/test_broken_prompt.py`) is explicitly a harness-mechanism test, clearly
  documented as such, and never counted toward golden-set routing accuracy.

### OpenTelemetry-style live tracing with a recording proxy
- Pros: transport-level, no per-SDK-call code needed.
- Cons: SPEC.md's Assumption 5 explicitly rules out a collector/exporter for v1; this would also
  still need a cassette-equivalent storage format underneath, just moved to a different layer.
- Rejected as out of scope for v1.

## Consequences
- Every committed cassette is tied to the exact prompt hash, model, and tool schema that produced
  it — SPEC.md § Boundaries lists "re-recording cassettes in bulk" and "changing the agent's
  model" as "ask first" actions, because both invalidate every committed cassette and baseline at
  once.
- The regression suite (`tests/regression/test_golden_set.py`) genuinely cannot pass until
  cassettes are recorded for all 60 golden cases — this is the exact blocker tracked in
  `docs/known-gaps.md`, not a bug in this design.
- A cassette miss is always a named, actionable failure (the key and the re-record command), never
  a silent fallback to the network — this is what makes "no API key present" a safe assumption for
  CI to assert rather than merely hope for.
