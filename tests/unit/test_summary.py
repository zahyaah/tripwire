"""Suite-level run summary (tasks/todo.md Task 16): totals sum from per-case figures, JSON
round-trips through its own schema, Markdown renders something PR-comment-shaped."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tripwire.assertions.runner import CaseResult
from tripwire.cost.rollup import RunRollup, StepCost
from tripwire.report.summary import RunSummary, build_summary, render_markdown, write_summary

_STARTED = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


def _case_result(case_id: str, run_id: str, *, passed: bool, cost: int, tokens: int) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        run_id=run_id,
        passed=passed,
        blocks_gate=not passed,
        loop_outcome="completed",
        loop_error=None,
        assertion_results=(),
        total_micro_dollars=cost,
        total_tokens=tokens,
    )


def _rollup(run_id: str, latencies: list[int]) -> RunRollup:
    return RunRollup(
        run_id=run_id,
        total_tokens=0,
        total_micro_dollars=0,
        agent_micro_dollars=0,
        judge_micro_dollars=0,
        total_latency_ms=sum(latencies),
        p95_step_latency_ms=max(latencies) if latencies else 0,
        step_costs=tuple(
            StepCost(step_index=i, micro_dollars=0, latency_ms=lat)
            for i, lat in enumerate(latencies)
        ),
    )


def _summary() -> RunSummary:
    results = [
        _case_result("faq-01", "run_1", passed=True, cost=500, tokens=100),
        _case_result("refund-01", "run_2", passed=False, cost=800, tokens=150),
        _case_result("faq-02", "run_3", passed=True, cost=300, tokens=80),
    ]
    rollups = {
        "run_1": _rollup("run_1", [50, 60]),
        "run_2": _rollup("run_2", [200, 90]),
        "run_3": _rollup("run_3", [40]),
    }
    return build_summary(
        suite_run_id="suite_test_1",
        model="test-model",
        llm_mode="replay",
        prompt_hash="deadbeef",
        started_at=_STARTED,
        case_intents={"faq-01": "faq", "refund-01": "refund_request", "faq-02": "faq"},
        case_results=results,
        rollups=rollups,
    )


def test_totals_equal_sum_of_per_case_figures() -> None:
    summary = _summary()
    assert summary.total_cases == 3
    assert summary.total_passed == 2
    assert summary.total_micro_dollars == 500 + 800 + 300
    assert summary.total_tokens == 100 + 150 + 80
    assert summary.overall_accuracy == 2 / 3


def test_per_intent_accuracy_is_correct() -> None:
    summary = _summary()
    by_intent = {row.intent: row for row in summary.per_intent}
    assert by_intent["faq"].passed == 2
    assert by_intent["faq"].total == 2
    assert by_intent["refund_request"].passed == 0
    assert by_intent["refund_request"].total == 1


def test_any_blocks_gate_is_true_when_a_case_failed() -> None:
    assert _summary().any_blocks_gate is True


def test_p95_step_latency_is_pooled_across_every_case_not_averaged_per_case() -> None:
    # Pooled latencies: [50, 60, 200, 90, 40] — nearest-rank p95 of 5 values is the 5th
    # (ceil(0.95*5) = ceil(4.75) = 5).
    summary = _summary()
    assert summary.p95_step_latency_ms == 200


def test_json_round_trips_through_its_own_schema() -> None:
    summary = _summary()
    reloaded = RunSummary.model_validate_json(summary.model_dump_json())
    assert reloaded == summary


def test_write_summary_produces_both_files(tmp_path: Path) -> None:
    summary = _summary()
    json_path, md_path = write_summary(summary, tmp_path / "suite_test_1")
    assert json_path.exists()
    assert md_path.exists()
    assert RunSummary.model_validate_json(json_path.read_text(encoding="utf-8")) == summary


def test_markdown_contains_every_case_and_the_headline_numbers() -> None:
    md = render_markdown(_summary())
    assert "faq-01" in md
    assert "refund-01" in md
    assert "faq-02" in md
    assert "1600" in md  # total cost
    assert "suite_test_1" in md


def test_a_case_missing_a_rollup_still_appears_with_zero_latency_contribution() -> None:
    results = [_case_result("faq-01", "run_1", passed=True, cost=500, tokens=100)]
    summary = build_summary(
        suite_run_id="suite_test_2",
        model="test-model",
        llm_mode="replay",
        prompt_hash="deadbeef",
        started_at=_STARTED,
        case_intents={"faq-01": "faq"},
        case_results=results,
        rollups={},  # no rollup available for run_1 (e.g. an incomplete trace)
    )
    assert summary.total_cases == 1
    assert summary.p95_step_latency_ms == 0
