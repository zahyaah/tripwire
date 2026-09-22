"""Roll a run's spans up into totals: tokens, cost, latency, split by step and by role.

Consumed by `report` (per-run summary) and `ci` (the regression gate's cost/latency thresholds),
so every number here has to be traceable back to a specific span — nothing here is estimated.
"""

from __future__ import annotations

import math
from typing import assert_never

from pydantic import BaseModel, ConfigDict, Field

from tripwire.core.records import AgentRunSpan, JudgeCallSpan, ModelCallSpan, Span, ToolCallSpan


class IncompleteRunError(Exception):
    """`rollup` was given spans with no `AgentRunSpan` root, so a run-level total can't be trusted.

    `total_latency_ms` has no independent recomputation path — it is the root span's own measured
    wall time (see `rollup`'s docstring). Defaulting it to 0 when the root span is missing would
    read as "instant success" for what might be a crashed or truncated run, which is exactly the
    silent-false-number failure SPEC.md forbids for cost and applies here to latency too.
    """


class DuplicateSpanError(Exception):
    """The same `span_id` appeared twice in the input. Silently summing it would double-count
    its cost, tokens, and latency — the same failure class as a duplicated charge."""


class SpanRunMismatchError(Exception):
    """A span's own `run_id` doesn't match the `run_id` `rollup` was called with.

    Catches the case where a caller accidentally concatenates spans from two different trace
    files (or a `TraceReader` bug returns the wrong slice) before they get silently aggregated
    under one run's cost figure.
    """


class StepCost(BaseModel):
    """One step's contribution to the run: cost summed, latency taken as the step's wall time.

    Multiple spans can share a `step_index` (parallel tool calls within one loop iteration, see
    SPEC-trace-core.md § Records). Cost sums across them — every dollar spent in the step counts.
    Latency takes the max, not the sum: concurrent calls overlap in wall-clock time, so summing
    would double-count time that didn't actually elapse twice.
    """

    model_config = ConfigDict(frozen=True)

    step_index: int = Field(ge=0)
    micro_dollars: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class RunRollup(BaseModel):
    """Aggregate cost, tokens, and latency for one run, derived entirely from its spans."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    total_tokens: int = Field(ge=0)
    total_micro_dollars: int = Field(ge=0)
    agent_micro_dollars: int = Field(ge=0)
    judge_micro_dollars: int = Field(ge=0)
    total_latency_ms: int = Field(ge=0)
    p95_step_latency_ms: int = Field(ge=0)
    step_costs: tuple[StepCost, ...]


def _p95_nearest_rank(values: list[int]) -> int:
    """95th percentile by the nearest-rank method: sort ascending, take the ceil(0.95n)-th value.

    Deterministic and dependency-free (no numpy/scipy — see SPEC.md § Tech Stack). For n <= 20
    this method's ceiling rounds up to the highest rank for any p >= 1 - 1/n, so a 10- or
    20-element run reports its single slowest step as p95 — expected behavior for the method, not
    a bug, and exactly what makes one abnormally slow step visible on a small run instead of
    getting averaged away.
    """
    if not values:
        return 0
    ordered = sorted(values)
    rank = math.ceil(0.95 * len(ordered))
    index = max(rank - 1, 0)
    return ordered[index]


def rollup(run_id: str, spans: list[Span]) -> RunRollup:
    """Compute a `RunRollup` from a run's spans.

    Totals are re-derived from `model_call`/`judge_call` spans rather than trusted from the
    `agent_run` span's own `total_micro_dollars` field — that field is the agent loop's
    self-reported running total, useful for the loop's own budget check, but `rollup` is the
    authoritative figure everything downstream (report, gate) relies on, so it is computed
    independently rather than propagating whatever the loop happened to accumulate.

    Raises `IncompleteRunError` if no `AgentRunSpan` is present (see that class's docstring),
    `SpanRunMismatchError` if a span's `run_id` doesn't match `run_id`, and `DuplicateSpanError`
    if the same `span_id` appears twice — all three are silent-wrong-number failure modes this
    function refuses to paper over.

    An errored call (`is_error=True`, e.g. a `model_call` that failed after the API had already
    processed it — see SPEC-trace-core.md § Model gateway) is still counted in every total: the
    call was still billed by the provider whether or not it succeeded, so excluding it would
    undercount real spend. This is a deliberate decision, not an oversight.
    """
    agent_micro_dollars = 0
    judge_micro_dollars = 0
    total_tokens = 0
    step_costs_by_index: dict[int, int] = {}
    step_latency_by_index: dict[int, int] = {}
    root_latency_ms: int | None = None
    seen_span_ids: set[str] = set()

    for span in spans:
        if span.run_id != run_id:
            raise SpanRunMismatchError(
                f"span {span.span_id!r} has run_id {span.run_id!r}, expected {run_id!r}"
            )
        if span.span_id in seen_span_ids:
            raise DuplicateSpanError(f"span_id {span.span_id!r} appears more than once")
        seen_span_ids.add(span.span_id)

        if isinstance(span, AgentRunSpan):
            root_latency_ms = span.latency_ms
            continue

        step_costs_by_index.setdefault(span.step_index, 0)
        step_latency_by_index[span.step_index] = max(
            step_latency_by_index.get(span.step_index, 0), span.latency_ms
        )

        if isinstance(span, ModelCallSpan):
            agent_micro_dollars += span.micro_dollars
            total_tokens += span.usage.total_tokens
            step_costs_by_index[span.step_index] += span.micro_dollars
        elif isinstance(span, JudgeCallSpan):
            judge_micro_dollars += span.micro_dollars
            total_tokens += span.usage.total_tokens
            step_costs_by_index[span.step_index] += span.micro_dollars
        elif isinstance(span, ToolCallSpan):
            pass  # carries no cost of its own (see SPEC-trace-core.md § Records)
        else:
            # Exhaustiveness guard: if `Span` grows a new kind, mypy strict flags this line
            # because `span` would no longer be narrowed to `Never` here — the new kind can't
            # silently fall through uncosted the way an unhandled `elif` chain would allow.
            assert_never(span)

    if root_latency_ms is None:
        raise IncompleteRunError(
            f"run {run_id!r}: no AgentRunSpan found among {len(spans)} span(s) — "
            "can't report a trustworthy total_latency_ms for an incomplete trace"
        )

    step_costs = tuple(
        StepCost(
            step_index=step_index,
            micro_dollars=step_costs_by_index[step_index],
            latency_ms=step_latency_by_index[step_index],
        )
        for step_index in sorted(step_costs_by_index)
    )

    return RunRollup(
        run_id=run_id,
        total_tokens=total_tokens,
        total_micro_dollars=agent_micro_dollars + judge_micro_dollars,
        agent_micro_dollars=agent_micro_dollars,
        judge_micro_dollars=judge_micro_dollars,
        total_latency_ms=root_latency_ms,
        p95_step_latency_ms=_p95_nearest_rank([s.latency_ms for s in step_costs]),
        step_costs=step_costs,
    )
