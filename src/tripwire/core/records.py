"""The trace schema: what a run is, what a step is.

This is the contract every other module reads or writes (see SPEC-trace-core.md § Contracts).
`agent` emits spans; `assertions`, `judge`, and `report` only ever read them. Nothing here knows
about inbox triage, matchers, or scoring — that boundary is what lets a second agent reuse this
module without a rewrite.

Span kinds are a discriminated union on `kind`, per api-and-interface-design's guidance for
representing variants explicitly: a consumer pattern-matches on `kind` and gets exhaustiveness
checking from mypy, instead of guessing which optional fields apply to which record.

Schema evolution is additive-only (new optional fields), per the same skill's "prefer addition
over modification" rule. `SCHEMA_VERSION` is bumped only for a breaking change, and bumping it is
an "ask first" action (SPEC-trace-core.md § Boundaries) — every consumer, and every committed
trace and cassette, depends on this shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class TokenUsage(BaseModel):
    """Usage reported by the model API for one call. See SPEC.md: `response.usage` only."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class RunRecord(BaseModel):
    """One agent run. Written once, as the header line of `runs/<run_id>/trace.jsonl`.

    `run_id` is the correlation id for every span in the file (observability-and-instrumentation
    §3): nothing below this line should need to be re-derived from context.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = SCHEMA_VERSION
    run_id: str
    suite_run_id: str | None = None
    case_id: str | None = None
    agent_name: str
    model: str
    prompt_hash: str
    harness_version: str
    llm_mode: Literal["live", "record", "replay"]
    started_at: datetime
    finished_at: datetime | None = None
    outcome: Literal["completed", "budget_exceeded", "error"] | None = None
    error: str | None = None


class _SpanBase(BaseModel):
    """Fields every span carries, independent of kind."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = SCHEMA_VERSION
    span_id: str
    parent_span_id: str | None
    run_id: str
    step_index: int = Field(ge=0)
    started_at: datetime
    latency_ms: int = Field(ge=0)
    is_error: bool = False


class AgentRunSpan(_SpanBase):
    """The root span of a run: one per `trace.jsonl`, parent of every other span in it."""

    kind: Literal["agent_run"] = "agent_run"
    step_count: int = Field(ge=0)
    total_micro_dollars: int = Field(ge=0)


class ModelCallSpan(_SpanBase):
    """One call to the agent's model, through the gateway (SPEC-trace-core.md § Model gateway)."""

    kind: Literal["model_call"] = "model_call"
    model: str
    stop_reason: str | None
    usage: TokenUsage
    micro_dollars: int = Field(ge=0)
    cassette_key: str
    cassette_hit: bool


class ToolCallSpan(_SpanBase):
    """One tool invocation. `arguments` is always a parsed object, never a JSON string."""

    kind: Literal["tool_call"] = "tool_call"
    tool_name: str
    arguments: dict[str, object]
    result_summary: str
    result_bytes: int = Field(ge=0)


class JudgeCallSpan(_SpanBase):
    """One LLM-as-judge call, priced and traced like any other model call, kept separable for
    cost rollups (agent cost vs. judge cost) even though the shape mirrors `ModelCallSpan`."""

    kind: Literal["judge_call"] = "judge_call"
    rubric_version: str
    model: str
    stop_reason: str | None
    usage: TokenUsage
    micro_dollars: int = Field(ge=0)
    cassette_key: str
    cassette_hit: bool


Span = Annotated[
    AgentRunSpan | ModelCallSpan | ToolCallSpan | JudgeCallSpan,
    Field(discriminator="kind"),
]
