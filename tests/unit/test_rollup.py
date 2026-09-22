"""Rollup: per-step cost/latency aggregation, agent-vs-judge cost split, p95 step latency."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tripwire.core.records import (
    AgentRunSpan,
    JudgeCallSpan,
    ModelCallSpan,
    TokenUsage,
    ToolCallSpan,
)
from tripwire.cost.rollup import (
    DuplicateSpanError,
    IncompleteRunError,
    SpanRunMismatchError,
    _p95_nearest_rank,
    rollup,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _model_call(
    step_index: int, micro_dollars: int, latency_ms: int, span_id: str
) -> ModelCallSpan:
    return ModelCallSpan(
        span_id=span_id,
        parent_span_id="sp_root",
        run_id="run_1",
        step_index=step_index,
        started_at=_NOW,
        latency_ms=latency_ms,
        model="nvidia/nemotron-3.5-lightning",
        stop_reason="tool_calls",
        usage=TokenUsage(prompt_tokens=100, completion_tokens=20),
        micro_dollars=micro_dollars,
        cassette_key="key",
        cassette_hit=True,
    )


def _judge_call(step_index: int, micro_dollars: int, latency_ms: int) -> JudgeCallSpan:
    return JudgeCallSpan(
        span_id="sp_judge",
        parent_span_id="sp_root",
        run_id="run_1",
        step_index=step_index,
        started_at=_NOW,
        latency_ms=latency_ms,
        rubric_version="v1",
        model="nvidia/nemotron-3.5-lightning",
        stop_reason="stop",
        usage=TokenUsage(prompt_tokens=500, completion_tokens=200),
        micro_dollars=micro_dollars,
        cassette_key="judge-key",
        cassette_hit=True,
    )


def _tool_call(step_index: int, latency_ms: int, span_id: str) -> ToolCallSpan:
    return ToolCallSpan(
        span_id=span_id,
        parent_span_id="sp_model",
        run_id="run_1",
        step_index=step_index,
        started_at=_NOW,
        latency_ms=latency_ms,
        tool_name="get_thread",
        arguments={},
        result_summary="ok",
        result_bytes=10,
    )


def test_p95_nearest_rank_on_known_10_element_list() -> None:
    # nearest-rank, n=10: rank = ceil(0.95*10) = 10 -> the 10th (largest) value. Documented in
    # rollup.py: for n <= 20 this method reports the single slowest value as p95.
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 1000]
    assert _p95_nearest_rank(values) == 1000


def test_p95_nearest_rank_on_larger_list_is_not_always_the_max() -> None:
    # n=100: rank = ceil(95) = 95 -> the 95th smallest of 100 ascending values.
    values = list(range(1, 101))  # 1..100
    assert _p95_nearest_rank(values) == 95


def test_p95_nearest_rank_empty_is_zero() -> None:
    assert _p95_nearest_rank([]) == 0


def test_rollup_separates_agent_and_judge_cost() -> None:
    root = AgentRunSpan(
        span_id="sp_root",
        parent_span_id=None,
        run_id="run_1",
        step_index=0,
        started_at=_NOW,
        latency_ms=5000,
        step_count=2,
        total_micro_dollars=999,  # deliberately wrong — rollup must not trust this field
    )
    spans = [
        root,
        _model_call(0, micro_dollars=1000, latency_ms=200, span_id="sp_m0"),
        _model_call(1, micro_dollars=1500, latency_ms=300, span_id="sp_m1"),
        _judge_call(1, micro_dollars=4000, latency_ms=800),
    ]
    result = rollup("run_1", spans)
    assert result.agent_micro_dollars == 2500
    assert result.judge_micro_dollars == 4000
    assert result.total_micro_dollars == 6500
    assert result.total_latency_ms == 5000  # from the root span, not summed from children


def _root(latency_ms: int = 1) -> AgentRunSpan:
    return AgentRunSpan(
        span_id="sp_root",
        parent_span_id=None,
        run_id="run_1",
        step_index=0,
        started_at=_NOW,
        latency_ms=latency_ms,
        step_count=1,
        total_micro_dollars=0,
    )


def test_rollup_totals_tokens_across_model_and_judge_calls() -> None:
    spans = [
        _root(),
        _model_call(0, micro_dollars=1, latency_ms=1, span_id="sp_m0"),  # 100+20 = 120
        _judge_call(0, micro_dollars=1, latency_ms=1),  # 500+200 = 700
    ]
    result = rollup("run_1", spans)
    assert result.total_tokens == 120 + 700


def test_rollup_step_cost_sums_parallel_calls_but_takes_max_latency() -> None:
    # Step 1 has one model_call and two parallel tool_calls (SPEC-trace-core.md § Records).
    spans = [
        _root(),
        _model_call(1, micro_dollars=100, latency_ms=50, span_id="sp_m1"),
        _tool_call(1, latency_ms=30, span_id="sp_t1a"),
        _tool_call(1, latency_ms=90, span_id="sp_t1b"),  # slowest of the step
    ]
    result = rollup("run_1", spans)
    assert len(result.step_costs) == 1
    step = result.step_costs[0]
    assert step.step_index == 1
    assert step.micro_dollars == 100  # tool calls carry no cost of their own
    assert step.latency_ms == 90  # max, not sum (30+90=120 would double-count overlap)


def test_rollup_p95_uses_per_step_latency_not_per_span() -> None:
    # 10 steps, each with exactly one model_call, latencies 10..100 in steps of 10.
    spans: list[object] = [_root()]
    spans += [
        _model_call(i, micro_dollars=10, latency_ms=(i + 1) * 10, span_id=f"sp_m{i}")
        for i in range(10)
    ]
    result = rollup("run_1", spans)  # type: ignore[arg-type]
    assert len(result.step_costs) == 10
    assert result.p95_step_latency_ms == 100  # nearest-rank on n=10 -> the max


def test_rollup_with_no_agent_run_span_raises_instead_of_defaulting_to_zero() -> None:
    # A silent 0 would read as "instant success" for what might be a crashed/truncated run.
    spans = [_model_call(0, micro_dollars=5, latency_ms=10, span_id="sp_m0")]
    with pytest.raises(IncompleteRunError, match="no AgentRunSpan"):
        rollup("run_1", spans)


def test_rollup_rejects_span_from_a_different_run() -> None:
    spans = [_model_call(0, micro_dollars=5, latency_ms=10, span_id="sp_m0")]
    spans[0] = spans[0].model_copy(update={"run_id": "run_other"})
    with pytest.raises(SpanRunMismatchError):
        rollup("run_1", spans)


def test_rollup_rejects_duplicate_span_id() -> None:
    span = _model_call(0, micro_dollars=5, latency_ms=10, span_id="sp_dup")
    with pytest.raises(DuplicateSpanError, match="sp_dup"):
        rollup("run_1", [span, span])
