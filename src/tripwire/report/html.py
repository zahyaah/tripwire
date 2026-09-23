"""Self-contained HTML trace viewer: one file per run, inlined CSS and JS, no network requests,
opens offline from a CI artifact download. See tasks/todo.md Task 15.

Reads only what's handed to it — a `RunRecord`, its spans, and optionally the assertion results
and judge score already computed elsewhere (`run_case`'s `CaseResult`, `tripwire calibrate`'s
`JudgeScore`) — the same "report only reads recorded data, never a live agent" boundary as every
other module here (CAPABILITY-MAP.md). A crashed run's trace is missing its closing
`AgentRunSpan` (the loop writes it last, after every step) — this still renders, deriving totals
from whichever spans are present instead of requiring the root span.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Template
from pydantic import BaseModel, ConfigDict

from tripwire.assertions.results import StepAssertionResult
from tripwire.core.records import (
    AgentRunSpan,
    JudgeCallSpan,
    ModelCallSpan,
    RunRecord,
    Span,
    ToolCallSpan,
)
from tripwire.judge.calibration import DimensionReport
from tripwire.judge.scores import JudgeScore

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "trace.html.j2"


class StepView(BaseModel):
    """Every span sharing a `step_index` (excluding the root `AgentRunSpan`), plus the
    assertions attached to that step."""

    model_config = ConfigDict(frozen=True)

    step_index: int
    model_call: ModelCallSpan | None = None
    tool_calls: tuple[ToolCallSpan, ...] = ()
    assertions: tuple[StepAssertionResult, ...] = ()

    @property
    def micro_dollars(self) -> int:
        return self.model_call.micro_dollars if self.model_call else 0

    @property
    def latency_ms(self) -> int:
        model_latency = self.model_call.latency_ms if self.model_call else 0
        return model_latency + sum(t.latency_ms for t in self.tool_calls)

    @property
    def has_error(self) -> bool:
        if self.model_call is not None and self.model_call.is_error:
            return True
        return any(t.is_error for t in self.tool_calls)

    @property
    def all_assertions_passed(self) -> bool:
        return all(a.passed for a in self.assertions)


class JudgeView(BaseModel):
    """One `judge_call` span, plus its persisted answer and the calibration agreement figures
    for the dimensions that answer covers, when either is available."""

    model_config = ConfigDict(frozen=True)

    span: JudgeCallSpan
    score: JudgeScore | None = None
    dimension_reports: tuple[DimensionReport, ...] = ()


@dataclass
class _StepAccumulator:
    model_call: ModelCallSpan | None = None
    tool_calls: list[ToolCallSpan] = field(default_factory=list)


def _group_steps(
    spans: Sequence[Span], assertions: Sequence[StepAssertionResult]
) -> list[StepView]:
    assertions_by_step: dict[int, list[StepAssertionResult]] = {}
    for assertion in assertions:
        if assertion.step_index is not None:
            assertions_by_step.setdefault(assertion.step_index, []).append(assertion)

    accumulators: dict[int, _StepAccumulator] = {}
    order: list[int] = []
    for span in spans:
        if isinstance(span, AgentRunSpan | JudgeCallSpan):
            continue
        if span.step_index not in accumulators:
            accumulators[span.step_index] = _StepAccumulator()
            order.append(span.step_index)
        acc = accumulators[span.step_index]
        if isinstance(span, ModelCallSpan):
            acc.model_call = span
        elif isinstance(span, ToolCallSpan):
            acc.tool_calls.append(span)

    return [
        StepView(
            step_index=index,
            model_call=accumulators[index].model_call,
            tool_calls=tuple(accumulators[index].tool_calls),
            assertions=tuple(assertions_by_step.get(index, [])),
        )
        for index in sorted(order)
    ]


def _sum_micro_dollars(spans: Sequence[Span]) -> int:
    return sum(
        span.micro_dollars for span in spans if isinstance(span, ModelCallSpan | JudgeCallSpan)
    )


def render_trace_html(
    run: RunRecord,
    spans: Sequence[Span],
    *,
    assertions: Sequence[StepAssertionResult] = (),
    judge_score: JudgeScore | None = None,
    dimension_reports: Sequence[DimensionReport] = (),
) -> str:
    """Render one run's trace as a complete, self-contained HTML document (a string — writing it
    to `runs/<run_id>/trace.html` is the caller's job, per tasks/todo.md Task 16's `tripwire
    report`)."""
    root = next((s for s in spans if isinstance(s, AgentRunSpan)), None)
    steps = _group_steps(spans, assertions)
    judge_views = [
        JudgeView(span=span, score=judge_score, dimension_reports=tuple(dimension_reports))
        for span in spans
        if isinstance(span, JudgeCallSpan)
    ]
    case_wide_assertions = tuple(a for a in assertions if a.step_index is None)
    total_latency_ms = sum(s.latency_ms for s in spans if not isinstance(s, AgentRunSpan))

    template = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"), autoescape=True)
    return str(
        template.render(
            run=run,
            is_partial=root is None,
            step_count=root.step_count if root is not None else len(steps),
            total_micro_dollars=(
                root.total_micro_dollars if root is not None else _sum_micro_dollars(spans)
            ),
            total_latency_ms=total_latency_ms,
            steps=steps,
            judge_views=judge_views,
            case_wide_assertions=case_wide_assertions,
            has_assertions=bool(assertions),
            overall_passed=all(a.passed for a in assertions) if assertions else None,
        )
    )
