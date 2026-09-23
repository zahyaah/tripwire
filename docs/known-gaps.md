Written for: anyone auditing TripWire's golden set — why it isn't designed to show 100%.

# Known gaps

SPEC-synthetic-data.md requires at least one golden case the current agent is expected to fail,
committed and documented here rather than deleted or weakened. A golden set that always passes
proves nothing about the harness; it only proves the cases were chosen to be easy.

## Status: predicted, not yet empirically confirmed

Every claim below is a design prediction, not a measured result. **Cassette recording for the
60-case golden set is blocked by the Gemini free-tier daily quota** (20 requests/day/model; each
multi-step case needs 3-5 calls, so even the original 8 cases exceed the daily cap in one pass —
see the project memory note on this). Until cassettes are recorded and `uv run pytest -m
regression` actually runs each case, this file states what the harness is *designed* to catch,
not what it has *observed*. Once real runs exist, this file will be updated to state the actual
pass/fail result with a run id and trace link, and any prediction that turned out wrong will be
corrected here, not quietly dropped.

## The designed-hard cases

### `angry-escalation-reveal-01`, `angry-escalation-gen-02`, `angry-escalation-gen-04`

Three cases (`data/golden/angry_escalation.yaml`) use threads from the synthetic corpus where an
angry-sounding opening message is followed by a calmer final message that reveals the real,
small ask (SPEC-synthetic-data.md's "an angry customer whose actual question is a FAQ"
adversarial pattern; see `agents/inbox_triage/prompt.md`'s explicit instruction to read to the
end before reacting to tone).

Each case forbids `escalate` and expects `action: replied` — the correct behavior is to notice
the de-escalation and answer the real question, not to escalate on the opening line's tone alone.

**Prediction:** at least one of these three fails on the first real run — either because the
model escalates on the angry opening without reading further, or because it reads the whole
thread but still defers to tone. This is exactly the kind of failure a tone-reactive agent (or a
harness that only checked "was the customer angry" rather than reading the full thread) would
produce, and it is why these cases exist.

### `refund-outside-policy-01`, `refund-request-gen-02`, `refund-request-gen-04`,
`refund-request-gen-06` (4 refund cases against corpus threads with a missing order)

These reference `manifest.missing_order_thread_ids` — orders the corpus deliberately does not
have a record for. The correct behavior is `lookup_order` returning not-found, then a reply that
does not promise a refund against an order that couldn't be verified.

**Prediction:** lower risk of failure than the angry-escalation cases (the corpus itself is a
harder distractor than the tool result), but a model that hallucinates a successful lookup, or
drafts a refund confirmation before checking, would fail here. Watching for this on the first
real run.

## What happens if a prediction is wrong

If a case above turns out to pass on the first real run, that's recorded here as a corrected
prediction, and the case stays in the golden set — a hard case that currently passes is still
useful as a regression guard against a future prompt change breaking it. If a case fails in a way
not described above, that failure gets its own entry here with the actual assertion output.

Golden cases are never deleted or weakened to make the suite pass (SPEC.md § Boundaries): a
"never" case failing means the case did its job.
