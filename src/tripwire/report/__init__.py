"""Self-contained HTML trace viewer and run summaries. See tasks/todo.md Tasks 15-16."""

from __future__ import annotations

from tripwire.report.gate import (
    DEFAULT_THRESHOLDS,
    GateResult,
    GateThresholds,
    GateViolation,
    evaluate_gate,
    format_gate_result,
    update_baseline,
)
from tripwire.report.html import JudgeView, StepView, render_trace_html
from tripwire.report.summary import (
    CaseSummary,
    IntentAccuracy,
    RunSummary,
    build_summary,
    render_markdown,
    write_summary,
)

__all__ = [
    "DEFAULT_THRESHOLDS",
    "CaseSummary",
    "GateResult",
    "GateThresholds",
    "GateViolation",
    "IntentAccuracy",
    "JudgeView",
    "RunSummary",
    "StepView",
    "build_summary",
    "evaluate_gate",
    "format_gate_result",
    "render_markdown",
    "render_trace_html",
    "update_baseline",
    "write_summary",
]
