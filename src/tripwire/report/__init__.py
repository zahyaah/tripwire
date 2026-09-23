"""Self-contained HTML trace viewer and run summaries. See tasks/todo.md Tasks 15-16."""

from __future__ import annotations

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
    "CaseSummary",
    "IntentAccuracy",
    "JudgeView",
    "RunSummary",
    "StepView",
    "build_summary",
    "render_markdown",
    "render_trace_html",
    "write_summary",
]
