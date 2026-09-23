"""trace-core: run/span schema, ids, and the JSONL trace store.

Public surface only — consumers (agent, assertions, judge, report) import from here, not from
the individual submodules, so the module's contract stays reviewable in one place.
"""

from __future__ import annotations

from tripwire.core.ids import new_run_id, new_span_id
from tripwire.core.records import (
    SCHEMA_VERSION,
    AgentRunSpan,
    JudgeCallSpan,
    ModelCallSpan,
    RunRecord,
    Span,
    TokenUsage,
    ToolCallSpan,
)
from tripwire.core.runner import finish_run, prompt_hash, start_run
from tripwire.core.store import (
    TraceCorruptError,
    TraceReader,
    TraceWriter,
    UnsupportedSchemaVersionError,
    default_trace_path,
)

__all__ = [
    "SCHEMA_VERSION",
    "AgentRunSpan",
    "JudgeCallSpan",
    "ModelCallSpan",
    "RunRecord",
    "Span",
    "TokenUsage",
    "ToolCallSpan",
    "TraceCorruptError",
    "TraceReader",
    "TraceWriter",
    "UnsupportedSchemaVersionError",
    "default_trace_path",
    "finish_run",
    "new_run_id",
    "new_span_id",
    "prompt_hash",
    "start_run",
]
