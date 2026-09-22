# Spec: `trace-core`

Module id `trace-core` from `CAPABILITY-MAP.md`. Depends on nothing. Stack, commands, code style,
and boundaries are inherited from `SPEC.md`; only module-specific requirements appear here.

## Objective

Provide the generic recording substrate every other module reads: what a run is, what a step is,
what each step cost, how long it took, and how a model call can be replayed exactly. `trace-core`
knows nothing about inbox triage, assertions, or judging. It is the reason a second agent can be
evaluated later without rewriting the harness.

Consumers: `agent` (emits spans), `assertions` and `judge` (read spans), `report` (renders spans),
`ci` (thresholds over rollups).

## Scope

In scope: run and span schemas, run ids, the JSONL trace store, the price table, usage rollups,
and the NVIDIA-catalog gateway with cassette record/replay.

Out of scope: anything agent-specific, any assertion logic, any scoring, any HTML.

## Contracts

### Records

One `RunRecord` per agent run, plus an append-only stream of spans.

```python
class RunRecord(BaseModel):
    schema_version: int
    run_id: str              # sortable: <utc timestamp>-<short random>
    suite_run_id: str | None # set when the run is part of a suite execution
    case_id: str | None      # golden case id, when the run came from one
    agent_name: str
    model: str
    effort: str
    prompt_hash: str
    harness_version: str
    llm_mode: Literal["live", "record", "replay"]
    started_at: datetime
    finished_at: datetime | None
    outcome: Literal["completed", "budget_exceeded", "error"]
    error: str | None
```

Span kinds, all carrying `span_id`, `parent_span_id`, `run_id`, `step_index`, `started_at`,
`latency_ms`, `is_error`:

| Kind | Additional fields |
|---|---|
| `agent_run` | `step_count`, `total_micro_dollars` |
| `model_call` | `model`, `stop_reason`, `usage` (prompt tokens, completion tokens), `micro_dollars`, `cassette_key`, `cassette_hit` |
| `tool_call` | `tool_name`, `arguments` (parsed dict), `result_summary`, `result_bytes` |
| `judge_call` | `rubric_version`, plus every `model_call` field |

Rules:

- `step_index` is the agent loop iteration the span belongs to, starting at 0. Parallel tool calls
  within one iteration share a `step_index` and differ by `span_id`.
- `arguments` is always a parsed dict, never a JSON string. Parsing happens at the loop boundary.
- No span field is optional-by-convenience: if a value cannot be known, the field is explicitly
  nullable and its absence is meaningful.

### Trace store

```
runs/<run_id>/trace.jsonl     # one JSON object per line: the run record first, then spans
runs/<suite_run_id>/summary.json   # written by `report`, not by this module
```

- `TraceWriter.append(span)` flushes per line, so a crashed process leaves a readable prefix.
- `TraceReader.load(run_id)` returns `(RunRecord, list[Span])` with typed spans.
- A truncated final line is skipped with a warning; any other malformed line raises.
- An unknown `schema_version` raises `UnsupportedSchemaVersionError`. Committed traces are never
  silently re-interpreted under a new schema.

### Cost

- Price table entries: `model`, `as_of` date, and rates for prompt and completion tokens. Sourced
  from the NVIDIA API catalog's published pricing for the model in use; if the catalog endpoint is
  free-tier/preview with no published rate, the entry is an explicit zero-price row, not an
  omitted one, so `rollup` still runs and the report states plainly that cost is untracked rather
  than implying it is free-to-run in general (see `SPEC.md` Open Question 7).
- `price_call(model, usage) -> int` returns micro-dollars, rounding half-up at the micro-dollar.
- Unknown model id raises `UnknownModelError`. Defaulting to zero for an *unknown* model is
  forbidden: a silent zero there turns a cost regression into a passing build. An explicit,
  documented zero-price row for a *known* free-tier model is not the same thing.
- `rollup(run) -> RunRollup`: totals for tokens and micro-dollars, per-step breakdown, total
  latency, p95 step latency, and agent cost separated from judge cost.

### Model gateway

```python
gateway.create(request: ModelRequest) -> openai.types.chat.ChatCompletion
```

- Built on the `openai` Python SDK, client constructed with
  `base_url="https://integrate.api.nvidia.com/v1"` and the NVIDIA API key.
- Emits exactly one `model_call` span per call, including on failure (`is_error=True`).
- No thinking-budget or effort parameter is sent — this model has no such concept; the gateway
  passes only `model`, `messages`, `tools`, `tool_choice`, `response_format` (when used), and
  `max_tokens`.
- Streaming is used whenever `max_tokens` is large enough to risk an HTTP timeout, accumulating
  chunks into the same response shape a non-streaming call returns.
- Errors are caught most-specific-first by `openai` SDK exception class (`NotFoundError`,
  `RateLimitError`, `APIStatusError`, `APIConnectionError`); retryable and non-retryable failures
  are distinguished in the span.
- Whether tool calls arrive as native `tool_calls` blocks or must be requested via a
  prompted-JSON fallback is confirmed against the model card before this task starts (`SPEC.md`
  Open Question 6); either way the gateway's return shape to callers is the same, so the fallback
  is isolated inside the gateway, not leaked to `agent`.

### Cassettes

- Key: a stable SHA-256 over the canonicalized semantically relevant request — model, system
  prompt, messages, tool definitions, `tool_choice`, `response_format`. Dict key order and
  insignificant whitespace do not change the key. Request ids, timestamps, and retry counts are
  excluded.
- Stored at `fixtures/cassettes/<key>.json`: the recorded response content, `usage`, `stop_reason`,
  and the measured `latency_ms` from the recording.
- Replay restores recorded usage and recorded latency, so cost and latency metrics remain real
  numbers taken from real calls rather than invented ones.
- `replay` mode never touches the network. A miss raises `CassetteMissError` printing the key, the
  case id if known, and the exact re-record command.
- `record` mode overwrites an existing cassette only for the keys it actually calls.

## Testing Strategy

All tests are unit tests, no network, under 5 seconds total.

- Round-trip: a 4-step run with parallel tool calls writes and reads back to identical objects.
- Truncation: a trace whose last line is cut mid-object loads with a warning and the prior spans.
- Schema guard: a trace with a bumped `schema_version` raises.
- Cost: three hand-computed cases — plain prompt/completion, a zero-price known model, an unknown
  model — matching to the micro-dollar; unknown model raises, zero-price model does not.
- Rollup: p95 step latency on a known 10-step latency list; judge cost excluded from agent cost.
- Cassette key: reordering dict keys and reformatting whitespace does not change the key; changing
  the system prompt or a tool schema does change it.
- Gateway: record then replay against a fake transport yields identical content, usage, and span
  cost; a modified request misses; no thinking/effort parameter ever appears in a built request.

## Success Criteria

- [ ] A recorded model call replays with identical content, usage, and latency, and its priced
      cost matches a hand-computed value.
- [ ] A crashed run's partial trace is still readable and renderable.
- [ ] The span schema supports the agent loop, the judge, and the report without per-consumer
      fields leaking into `trace-core`.
- [ ] `trace-core` imports nothing from `agents/`, `assertions`, `judge`, or `report`, enforced by
      a test.
- [ ] `uv run mypy src/tripwire/core src/tripwire/cost src/tripwire/llm` clean under strict mode.

## Boundaries (module-specific)

- **Always:** flush every span as it happens; record the cassette key on every `model_call`.
- **Ask first:** any change to the span schema after the Phase 1 checkpoint freeze; any bulk
  cassette re-record.
- **Never:** import a consumer module; price an unknown model as zero; make a live call in
  `replay` mode; use a third-party tokenizer for token counts — `response.usage` only.

## Open Questions

1. Cassette storage: one file per key (simple, many files) versus one file per case (fewer files,
   coarser invalidation). Current assumption: one file per key.
2. Whether to record the full tool result body or a truncated summary in `tool_call` spans when a
   result is large. Current assumption: store the full body up to 16 KB, then truncate with a
   recorded byte count.
