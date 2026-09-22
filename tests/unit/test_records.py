"""Schema-level tests: discriminated union parsing, id sortability, constraint enforcement."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from tripwire.core.ids import new_run_id, new_span_id
from tripwire.core.records import ModelCallSpan, Span, TokenUsage, ToolCallSpan

_span_adapter: TypeAdapter[Span] = TypeAdapter(Span)


def test_run_id_is_sortable_by_creation_time() -> None:
    earlier = new_run_id(datetime(2026, 1, 1, tzinfo=UTC))
    later = new_run_id(datetime(2026, 1, 2, tzinfo=UTC))
    assert earlier < later


def test_run_id_and_span_id_are_distinct_and_prefixed() -> None:
    run_id = new_run_id()
    span_id = new_span_id()
    assert run_id.startswith("run_")
    assert span_id.startswith("sp_")
    assert run_id != span_id


def test_token_usage_total() -> None:
    usage = TokenUsage(prompt_tokens=100, completion_tokens=40)
    assert usage.total_tokens == 140


def test_span_union_discriminates_on_kind() -> None:
    tool_span = ToolCallSpan(
        span_id="sp_1",
        parent_span_id="sp_0",
        run_id="run_1",
        step_index=0,
        started_at=datetime.now(UTC),
        latency_ms=12,
        tool_name="lookup_customer",
        arguments={"email": "casey@example.net"},
        result_summary="found",
        result_bytes=42,
    )
    reparsed = _span_adapter.validate_python(tool_span.model_dump(mode="json"))
    assert isinstance(reparsed, ToolCallSpan)
    assert reparsed.tool_name == "lookup_customer"


def test_model_call_span_requires_usage() -> None:
    with pytest.raises(ValidationError):
        ModelCallSpan.model_validate(
            {
                "span_id": "sp_1",
                "parent_span_id": None,
                "run_id": "run_1",
                "step_index": 0,
                "started_at": datetime.now(UTC).isoformat(),
                "latency_ms": 10,
                "model": "nvidia/nemotron-3.5-lightning",
                "stop_reason": "stop",
                "micro_dollars": 0,
                "cassette_key": "abc",
                "cassette_hit": True,
                # usage omitted deliberately
            }
        )


def test_negative_latency_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolCallSpan(
            span_id="sp_1",
            parent_span_id=None,
            run_id="run_1",
            step_index=0,
            started_at=datetime.now(UTC),
            latency_ms=-1,
            tool_name="x",
            arguments={},
            result_summary="",
            result_bytes=0,
        )


def test_spans_are_frozen() -> None:
    span = ToolCallSpan(
        span_id="sp_1",
        parent_span_id=None,
        run_id="run_1",
        step_index=0,
        started_at=datetime.now(UTC),
        latency_ms=1,
        tool_name="x",
        arguments={},
        result_summary="",
        result_bytes=0,
    )
    with pytest.raises(ValidationError):
        span.tool_name = "y"  # type: ignore[misc]
