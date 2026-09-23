# ADR-0001: Model provider — Gemini 3.8-flash via its OpenAI-compatible endpoint

## Status
Accepted (supersedes two earlier choices, both abandoned before implementation completed for
that provider)

## Date
2026-09-23

## Context
TripWire needs one concrete model provider for the Inbox Triage Agent and the judge: something
reachable through the `openai` Python SDK (SPEC.md's stack choice, to keep the gateway
provider-agnostic in shape), with native tool/function calling, and cheap or free enough to
support a 60-case golden set plus ~120 judge-scored runs during development without a real budget.

The provider changed twice over the course of building this harness:

1. **Anthropic** (original plan) — dropped before implementation; the project moved to an
   OpenAI-compatible target instead so the gateway's request/response shape matches the `openai`
   SDK directly rather than translating Anthropic's own block-content format.
2. **NVIDIA API catalog**, `nvidia/nemotron-3.5-lightning` — the plan documented in this repo's
   `SPEC.md` through Task 3. Dropped per explicit user direction before any code was written
   against it.
3. **Google Gemini**, `gemini-3.8-flash`, via `https://generativelanguage.googleapis.com/v1beta/openai/`
   — the provider actually implemented, tested, and used for every task from Task 4 onward.

## Decision
Use Gemini's OpenAI-compatible endpoint, model id `gemini-3.8-flash`, for both the agent and the
judge.

Every fact about this endpoint used in the code is source-verified against the live API, not
assumed from documentation:

- The model id itself: the docs' own listed `gemini-3-flash` returned 404 against a real call;
  `gemini-3.8-flash` was confirmed via `client.models.list()`.
- Native `tool_calls` with `strict: true` function tools: confirmed live, returned
  `finish_reason="tool_calls"` with a correctly-shaped `tool_calls[0].function`.
- `max_tokens` works; `max_completion_tokens` is untested on this endpoint, so the gateway uses
  the former.
- `usage.total_tokens` exceeds `prompt_tokens + completion_tokens` by 40-90%, with
  `*_tokens_details` fields left null — hidden "thinking" tokens billed but not itemized through
  the compat layer.
- The free tier is rate-limited to 20 requests/day/model, not metered-and-billed. Confirmed via
  `client.models.list()`'s response and by hitting the limit directly during cassette recording.

## Alternatives Considered

### Anthropic (original plan)
- Pros: first-class agentic tool-use support, the provider TripWire's own author is most familiar
  with.
- Cons: not OpenAI-compatible — the gateway would need to translate between Anthropic's
  content-block message format and every other part of this codebase's OpenAI-shaped
  request/response types (`ModelRequest`, `ChatCompletion`).
- Rejected: before any gateway code was written, in favor of an OpenAI-compatible target to keep
  the request/response shape a direct passthrough.

### NVIDIA API catalog (`nvidia/nemotron-3.5-lightning`)
- Pros: OpenAI-compatible, tool-calling support documented on the model's own page.
- Cons: catalog model id string was unconfirmed even from the vendor's own sample code (`model=""`,
  populated by page JS, not visible to a static fetch) — SPEC.md's Open Questions tracked this as
  unresolved through Task 3.
- Rejected: by explicit user direction, before Task 4 (the gateway) was implemented against it.
  No cost or behavior was ever measured against this provider.

## Consequences
- `PRICE_TABLE` (`src/tripwire/cost/prices.py`) has one real entry, `gemini-3.8-flash`, with
  `billable=False` — costs are tracked as zero-priced tokens, not a fabricated dollar figure
  (SPEC.md § Success criteria).
- `TokenUsage.reasoning_tokens` (`src/tripwire/core/records.py`) exists specifically to recover
  Gemini 3's hidden-thinking-token billing gap; see `usage_from_completion` in
  `src/tripwire/llm/gateway.py`.
- The 20-request/day/model quota is the reason cassette recording for the 60-case golden set is
  still incomplete (`docs/known-gaps.md`) — every case needing 3-5 calls exceeds the daily cap in
  a single pass, so recording has to happen in small batches across multiple days.
- A third provider swap would mean re-verifying every source-verified fact above against the new
  endpoint, and re-recording every committed cassette — this is why the endpoint and model id are
  listed under SPEC.md § Boundaries "Ask first."
