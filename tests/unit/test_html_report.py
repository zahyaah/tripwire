"""Self-contained HTML trace viewer (tasks/todo.md Task 15): no network asset references, a
partial (crashed) trace renders without erroring, a failed assertion's detail shows up."""

from __future__ import annotations

from datetime import UTC, datetime

from tripwire.assertions.results import make_result
from tripwire.core.records import (
    AgentRunSpan,
    ModelCallSpan,
    RunRecord,
    TokenUsage,
    ToolCallSpan,
)
from tripwire.report.html import render_trace_html

_STARTED = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


def _run(**overrides: object) -> RunRecord:
    base: dict[str, object] = {
        "run_id": "run_test_1",
        "agent_name": "inbox_triage",
        "model": "test-model",
        "prompt_hash": "deadbeefcafefeed",
        "harness_version": "0.1.0",
        "llm_mode": "replay",
        "started_at": _STARTED,
        "case_id": "case-1",
    }
    base.update(overrides)
    return RunRecord(**base)  # type: ignore[arg-type]


def _model_call_span(step_index: int = 0, *, is_error: bool = False) -> ModelCallSpan:
    return ModelCallSpan(
        span_id=f"sp_model_{step_index}",
        parent_span_id="sp_root",
        run_id="run_test_1",
        step_index=step_index,
        started_at=_STARTED,
        latency_ms=120,
        model="test-model",
        stop_reason="tool_calls",
        usage=TokenUsage(prompt_tokens=100, completion_tokens=20),
        micro_dollars=500,
        cassette_key="key1",
        cassette_hit=True,
        is_error=is_error,
    )


def _tool_call_span(step_index: int = 0, *, is_error: bool = False) -> ToolCallSpan:
    return ToolCallSpan(
        span_id=f"sp_tool_{step_index}",
        parent_span_id="sp_root",
        run_id="run_test_1",
        step_index=step_index,
        started_at=_STARTED,
        latency_ms=5,
        tool_name="get_thread",
        arguments={"thread_id": "thr_00001"},
        result_summary="error: not found" if is_error else "ok: thread found",
        result_bytes=42,
        is_error=is_error,
    )


def _root_span(step_count: int = 1, total_micro_dollars: int = 500) -> AgentRunSpan:
    return AgentRunSpan(
        span_id="sp_root",
        parent_span_id=None,
        run_id="run_test_1",
        step_index=0,
        started_at=_STARTED,
        latency_ms=1,
        step_count=step_count,
        total_micro_dollars=total_micro_dollars,
    )


def test_full_run_renders_no_network_asset_references() -> None:
    spans = [_model_call_span(), _tool_call_span(), _root_span()]
    html = render_trace_html(_run(), spans)
    assert "http://" not in html
    assert "https://" not in html
    assert "run_test_1" in html
    assert "get_thread" in html


def test_partial_trace_with_no_root_span_still_renders() -> None:
    # No AgentRunSpan at all — as a crashed mid-run process would leave behind.
    spans = [_model_call_span(), _tool_call_span()]
    html = render_trace_html(_run(), spans)
    assert "Partial trace" in html
    assert "run_test_1" in html


def test_totals_derive_from_spans_when_root_is_missing() -> None:
    spans = [_model_call_span(), _tool_call_span()]
    html = render_trace_html(_run(), spans)
    assert "500" in html  # the model call's own micro_dollars, since no root total exists


def test_failed_assertion_detail_is_rendered() -> None:
    spans = [_model_call_span(), _tool_call_span(), _root_span()]
    failing = make_result(
        assertion_id="tool_called:draft_reply",
        case_id="case-1",
        passed=False,
        expected="draft_reply",
        actual="get_thread",
        step_index=0,
    )
    html = render_trace_html(_run(), spans, assertions=[failing])
    assert "FAIL" in html
    assert "draft_reply" in html
    assert "One or more required assertions failed" in html


def test_passed_assertions_show_a_pass_banner() -> None:
    spans = [_model_call_span(), _tool_call_span(), _root_span()]
    passing = make_result(
        assertion_id="tool_called:get_thread",
        case_id="case-1",
        passed=True,
        expected="get_thread",
        actual="get_thread",
        step_index=0,
    )
    html = render_trace_html(_run(), spans, assertions=[passing])
    assert "All assertions passed" in html


def test_case_wide_assertion_with_no_step_index_is_rendered_separately() -> None:
    spans = [_model_call_span(), _tool_call_span(), _root_span()]
    case_wide = make_result(
        assertion_id="cost_budget",
        case_id="case-1",
        passed=False,
        expected="<= 1000",
        actual="1500",
        step_index=None,
    )
    html = render_trace_html(_run(), spans, assertions=[case_wide])
    assert "Case-wide assertions" in html
    assert "cost_budget" in html


def test_error_span_shows_an_error_badge() -> None:
    spans = [_tool_call_span(is_error=True), _root_span()]
    html = render_trace_html(_run(), spans)
    assert "ERROR" in html


def test_no_steps_at_all_renders_without_erroring() -> None:
    html = render_trace_html(_run(), [])
    assert "run_test_1" in html
    assert "No steps recorded" in html
