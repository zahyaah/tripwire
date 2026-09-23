"""Pass and fail path for each matcher (tasks/todo.md Task 9), plus the subsequence-vs-strict
order distinction and a regex argument match. All matchers here read only `list[Span]` — no
agent object is constructed anywhere in this file."""

from __future__ import annotations

from datetime import UTC, datetime

from tripwire.assertions.matchers import (
    match_cost_budget,
    match_final_outcome,
    match_max_calls,
    match_not_called,
    match_order,
    match_step_budget,
    match_tool_args,
    match_tool_called,
)
from tripwire.core.golden import ArgMatcher, ExpectedOutcome
from tripwire.core.records import AgentRunSpan, ModelCallSpan, TokenUsage, ToolCallSpan
from tripwire.cost.prices import PRICE_TABLE

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_RUN_ID = "run_test"
_CASE_ID = "case_test"


def _tool(
    tool_name: str,
    arguments: dict[str, object],
    *,
    step_index: int = 0,
    span_id: str = "sp_tool",
    is_error: bool = False,
) -> ToolCallSpan:
    return ToolCallSpan(
        span_id=span_id,
        parent_span_id="sp_root",
        run_id=_RUN_ID,
        step_index=step_index,
        started_at=_NOW,
        latency_ms=1,
        is_error=is_error,
        tool_name=tool_name,
        arguments=arguments,
        result_summary="{}",
        result_bytes=2,
    )


def _root(step_count: int = 1, span_id: str = "sp_root") -> AgentRunSpan:
    return AgentRunSpan(
        span_id=span_id,
        parent_span_id=None,
        run_id=_RUN_ID,
        step_index=0,
        started_at=_NOW,
        latency_ms=10,
        step_count=step_count,
        total_micro_dollars=0,
    )


def _model_call(step_index: int, micro_dollars: int, span_id: str = "sp_model") -> ModelCallSpan:
    return ModelCallSpan(
        span_id=span_id,
        parent_span_id="sp_root",
        run_id=_RUN_ID,
        step_index=step_index,
        started_at=_NOW,
        latency_ms=1,
        model="nvidia/nemotron-3.5-lightning",
        stop_reason="tool_calls",
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
        micro_dollars=micro_dollars,
        cassette_key="k",
        cassette_hit=True,
    )


# --- tool_called ---------------------------------------------------------------------------------


def test_tool_called_pass() -> None:
    spans = [_tool("get_thread", {"thread_id": "thr_1"})]
    result = match_tool_called(spans, _CASE_ID, "get_thread")
    assert result.passed is True
    assert result.detail == ""


def test_tool_called_fail_has_case_id_and_no_step_index() -> None:
    result = match_tool_called([], _CASE_ID, "get_thread")
    assert result.passed is False
    assert _CASE_ID in result.detail
    assert result.step_index is None


# --- not_called -----------------------------------------------------------------------------------


def test_not_called_pass() -> None:
    result = match_not_called([_tool("get_thread", {})], _CASE_ID, "escalate")
    assert result.passed is True


def test_not_called_fail_names_case_id_and_step_index() -> None:
    spans = [_tool("escalate", {"thread_id": "thr_1", "reason": "x"}, step_index=3)]
    result = match_not_called(spans, _CASE_ID, "escalate")
    assert result.passed is False
    assert _CASE_ID in result.detail
    assert "3" in result.detail


# --- max_calls ------------------------------------------------------------------------------------


def test_max_calls_pass_at_the_limit() -> None:
    spans = [_tool("lookup_order", {"order_id": "ord_1"}, span_id="a")]
    result = match_max_calls(spans, _CASE_ID, "lookup_order", 1)
    assert result.passed is True


def test_max_calls_fail_over_the_limit() -> None:
    spans = [
        _tool("lookup_order", {"order_id": "ord_1"}, span_id="a"),
        _tool("lookup_order", {"order_id": "ord_2"}, span_id="b"),
    ]
    result = match_max_calls(spans, _CASE_ID, "lookup_order", 1)
    assert result.passed is False
    assert "2 call(s)" in result.detail


# --- tool_args: exact / subset / regex ------------------------------------------------------------


def test_tool_args_exact_pass_and_fail() -> None:
    spans = [_tool("get_thread", {"thread_id": "thr_00184"})]
    args = {"thread_id": ArgMatcher(matcher="exact", value="thr_00184")}
    assert match_tool_args(spans, _CASE_ID, "get_thread", args).passed is True

    args_wrong = {"thread_id": ArgMatcher(matcher="exact", value="thr_99999")}
    result = match_tool_args(spans, _CASE_ID, "get_thread", args_wrong)
    assert result.passed is False
    assert _CASE_ID in result.detail


def test_tool_args_subset_pass_and_fail() -> None:
    spans = [_tool("draft_reply", {"thread_id": "thr_1", "body": "Here is your 30-day refund."})]
    args = {"body": ArgMatcher(matcher="subset", value="30-day")}
    assert match_tool_args(spans, _CASE_ID, "draft_reply", args).passed is True

    args_missing = {"body": ArgMatcher(matcher="subset", value="60-day")}
    assert match_tool_args(spans, _CASE_ID, "draft_reply", args_missing).passed is False


def test_tool_args_regex_pass_and_fail() -> None:
    body = "We'll issue a refund in 30 days."
    spans = [_tool("draft_reply", {"thread_id": "thr_1", "body": body})]
    args_match = {"body": ArgMatcher(matcher="regex", value=r"(?i)30[- ]day")}
    assert match_tool_args(spans, _CASE_ID, "draft_reply", args_match).passed is True

    args_no_match = {"body": ArgMatcher(matcher="regex", value=r"(?i)60[- ]day")}
    result = match_tool_args(spans, _CASE_ID, "draft_reply", args_no_match)
    assert result.passed is False


def test_tool_args_no_call_at_all_fails_with_clear_message() -> None:
    result = match_tool_args([], _CASE_ID, "draft_reply", {"body": ArgMatcher(value="x")})
    assert result.passed is False
    assert "no call to that tool at all" in result.detail


def test_tool_args_existential_over_multiple_calls() -> None:
    # The FIRST call to lookup_order has the wrong id; the SECOND has the right one. tool_args
    # must find the second, not just check the first and give up.
    spans = [
        _tool("lookup_order", {"order_id": "ord_wrong"}, span_id="a", step_index=0),
        _tool("lookup_order", {"order_id": "ord_right"}, span_id="b", step_index=1),
    ]
    args = {"order_id": ArgMatcher(matcher="exact", value="ord_right")}
    result = match_tool_args(spans, _CASE_ID, "lookup_order", args)
    assert result.passed is True
    assert result.step_index == 1


# --- order: subsequence vs strict -----------------------------------------------------------------


def test_order_subsequence_passes_with_calls_between() -> None:
    spans = [
        _tool("get_thread", {}, span_id="a", step_index=0),
        _tool("lookup_customer", {}, span_id="b", step_index=1),  # not in the expected sequence
        _tool("draft_reply", {}, span_id="c", step_index=2),
    ]
    result = match_order(spans, _CASE_ID, ["get_thread", "draft_reply"], mode="subsequence")
    assert result.passed is True


def test_order_strict_fails_with_calls_between_but_subsequence_would_pass() -> None:
    spans = [
        _tool("get_thread", {}, span_id="a", step_index=0),
        _tool("lookup_customer", {}, span_id="b", step_index=1),
        _tool("draft_reply", {}, span_id="c", step_index=2),
    ]
    strict_result = match_order(spans, _CASE_ID, ["get_thread", "draft_reply"], mode="strict")
    assert strict_result.passed is False
    subsequence_result = match_order(
        spans, _CASE_ID, ["get_thread", "draft_reply"], mode="subsequence"
    )
    assert subsequence_result.passed is True


def test_order_strict_passes_when_contiguous() -> None:
    spans = [
        _tool("get_thread", {}, span_id="a", step_index=0),
        _tool("draft_reply", {}, span_id="b", step_index=1),
    ]
    result = match_order(spans, _CASE_ID, ["get_thread", "draft_reply"], mode="strict")
    assert result.passed is True


def test_order_fails_when_out_of_order() -> None:
    spans = [
        _tool("draft_reply", {}, span_id="a", step_index=0),
        _tool("get_thread", {}, span_id="b", step_index=1),
    ]
    result = match_order(spans, _CASE_ID, ["get_thread", "draft_reply"], mode="subsequence")
    assert result.passed is False


# --- final_outcome --------------------------------------------------------------------------------


def test_final_outcome_action_derived_from_last_successful_action_tool() -> None:
    spans = [
        _tool("draft_reply", {"thread_id": "t", "body": "x"}, span_id="a", step_index=0),
        _tool("escalate", {"thread_id": "t", "reason": "y"}, span_id="b", step_index=1),
    ]
    # escalate happened last, so the derived action is "escalated", not "replied".
    result = match_final_outcome(spans, _CASE_ID, ExpectedOutcome(action="escalated"))
    assert result.passed is True
    result_wrong = match_final_outcome(spans, _CASE_ID, ExpectedOutcome(action="replied"))
    assert result_wrong.passed is False


def test_final_outcome_ignores_errored_action_calls() -> None:
    spans = [_tool("escalate", {"thread_id": "t"}, is_error=True)]
    result = match_final_outcome(spans, _CASE_ID, ExpectedOutcome(action="escalated"))
    assert result.passed is False  # the escalate call failed, so no_action is the real outcome


def test_final_outcome_label_checked_against_any_add_label_call() -> None:
    spans = [
        _tool("add_label", {"thread_id": "t", "label": "refund"}, span_id="a", step_index=0),
        _tool("draft_reply", {"thread_id": "t", "body": "x"}, span_id="b", step_index=1),
    ]
    result = match_final_outcome(
        spans, _CASE_ID, ExpectedOutcome(label="refund", action="replied")
    )
    assert result.passed is True
    result_wrong_label = match_final_outcome(spans, _CASE_ID, ExpectedOutcome(label="spam"))
    assert result_wrong_label.passed is False


def test_final_outcome_no_action_when_nothing_happened() -> None:
    result = match_final_outcome([], _CASE_ID, ExpectedOutcome(action="no_action"))
    assert result.passed is True


# --- step_budget ----------------------------------------------------------------------------------


def test_step_budget_pass_and_fail() -> None:
    spans = [_root(step_count=3)]
    assert match_step_budget(spans, _CASE_ID, 8).passed is True
    assert match_step_budget(spans, _CASE_ID, 2).passed is False


def test_step_budget_missing_agent_run_span_fails_with_clear_message() -> None:
    result = match_step_budget([], _CASE_ID, 8)
    assert result.passed is False
    assert "no agent_run span" in result.detail


# --- cost_budget ----------------------------------------------------------------------------------


def test_cost_budget_pass_and_fail() -> None:
    model = "nvidia/nemotron-3.5-lightning"  # billable=False in PRICE_TABLE -> always 0 cost
    assert model in PRICE_TABLE
    spans = [_root(), _model_call(0, micro_dollars=0)]
    assert match_cost_budget(spans, _CASE_ID, 100).passed is True


def test_cost_budget_fail_over_budget() -> None:
    spans = [
        _root(),
        _model_call(0, micro_dollars=500),
        _model_call(1, micro_dollars=600, span_id="m2"),
    ]
    result = match_cost_budget(spans, _CASE_ID, 1000)
    assert result.passed is False
    assert "1100 micro-dollars" in result.detail


def test_cost_budget_no_spans_fails_clearly() -> None:
    result = match_cost_budget([], _CASE_ID, 100)
    assert result.passed is False
    assert "no spans recorded" in result.detail


def test_cost_budget_incomplete_run_fails_clearly_not_crash() -> None:
    # A model_call with no agent_run span present -> rollup raises IncompleteRunError, which the
    # matcher must catch and turn into a failed result, not let propagate.
    spans = [_model_call(0, micro_dollars=10)]
    result = match_cost_budget(spans, _CASE_ID, 1000)
    assert result.passed is False
    assert "could not be computed" in result.detail


# --- blocks_build / required ----------------------------------------------------------------------


def test_advisory_failure_does_not_block_the_build() -> None:
    result = match_tool_called([], _CASE_ID, "get_thread", required=False)
    assert result.passed is False
    assert result.blocks_build is False


def test_required_failure_blocks_the_build() -> None:
    result = match_tool_called([], _CASE_ID, "get_thread", required=True)
    assert result.passed is False
    assert result.blocks_build is True
