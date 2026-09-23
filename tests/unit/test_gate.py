"""Regression gate (tasks/todo.md Task 17): synthetic summaries trip each threshold
individually, a clean summary passes, and a low-confidence judge dimension never blocks."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tripwire.judge.calibration import DimensionReport
from tripwire.report.gate import GateThresholds, evaluate_gate, format_gate_result, update_baseline
from tripwire.report.summary import CaseSummary, IntentAccuracy, RunSummary

_STARTED = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


def _summary(**overrides: object) -> RunSummary:
    base: dict[str, object] = {
        "suite_run_id": "suite_1",
        "model": "test-model",
        "llm_mode": "replay",
        "prompt_hash": "deadbeef",
        "started_at": _STARTED,
        "cases": (
            CaseSummary(
                case_id="faq-01",
                intent="faq",
                run_id="run_1",
                passed=True,
                blocks_gate=False,
                loop_outcome="completed",
                total_micro_dollars=1000,
                total_tokens=200,
            ),
        ),
        "per_intent": (IntentAccuracy(intent="faq", passed=1, total=1),),
        "total_cases": 1,
        "total_passed": 1,
        "total_micro_dollars": 1000,
        "total_tokens": 200,
        "p95_step_latency_ms": 100,
        "judge_dimensions": (),
        "any_blocks_gate": False,
    }
    base.update(overrides)
    return RunSummary(**base)  # type: ignore[arg-type]


def _dim(dimension: str, kappa: float, split: str = "holdout") -> DimensionReport:
    return DimensionReport(
        dimension=dimension,
        split=split,  # type: ignore[arg-type]
        n=10,
        raw_agreement=0.8,
        kappa=kappa,
        kappa_ci_lower=kappa - 0.1,
        kappa_ci_upper=kappa + 0.1,
        confidence="confident" if kappa >= 0.6 else "low_confidence",
    )


def test_identical_summaries_pass() -> None:
    baseline = _summary()
    current = _summary()
    result = evaluate_gate(current, baseline)
    assert result.passed
    assert result.violations == ()


def test_required_assertion_failure_always_blocks_even_with_a_lenient_baseline() -> None:
    baseline = _summary()
    current = _summary(
        any_blocks_gate=True,
        cases=(
            CaseSummary(
                case_id="faq-01",
                intent="faq",
                run_id="run_1",
                passed=False,
                blocks_gate=True,
                loop_outcome="completed",
                total_micro_dollars=1000,
                total_tokens=200,
            ),
        ),
    )
    result = evaluate_gate(current, baseline)
    assert not result.passed
    assert any(v.check == "required_assertions" for v in result.violations)


def test_accuracy_drop_beyond_tolerance_blocks() -> None:
    baseline = _summary(total_passed=10, total_cases=10)
    current = _summary(total_passed=8, total_cases=10)  # 100% -> 80%, a 20-point drop
    result = evaluate_gate(current, baseline, thresholds=GateThresholds(max_accuracy_drop=0.05))
    assert not result.passed
    assert any(v.check == "routing_accuracy" for v in result.violations)


def test_accuracy_drop_within_tolerance_passes() -> None:
    baseline = _summary(total_passed=10, total_cases=10)
    current = _summary(total_passed=10, total_cases=10)
    result = evaluate_gate(current, baseline, thresholds=GateThresholds(max_accuracy_drop=0.05))
    assert result.passed


def test_cost_regression_beyond_tolerance_blocks() -> None:
    baseline = _summary(total_micro_dollars=1000)
    current = _summary(total_micro_dollars=2000)  # +100%
    result = evaluate_gate(
        current, baseline, thresholds=GateThresholds(max_cost_regression_fraction=0.20)
    )
    assert not result.passed
    assert any(v.check == "total_cost" for v in result.violations)


def test_cost_with_zero_baseline_is_skipped_not_flagged_as_infinite_regression() -> None:
    baseline = _summary(total_micro_dollars=0)
    current = _summary(total_micro_dollars=500)
    result = evaluate_gate(current, baseline)
    assert not any(v.check == "total_cost" for v in result.violations)


def test_latency_regression_beyond_tolerance_blocks() -> None:
    baseline = _summary(p95_step_latency_ms=100)
    current = _summary(p95_step_latency_ms=300)  # +200%
    result = evaluate_gate(
        current, baseline, thresholds=GateThresholds(max_latency_regression_fraction=0.20)
    )
    assert not result.passed
    assert any(v.check == "p95_step_latency" for v in result.violations)


def test_judge_regression_on_a_trusted_dimension_blocks() -> None:
    baseline = _summary(judge_dimensions=(_dim("tone_match", 0.80),))
    current = _summary(judge_dimensions=(_dim("tone_match", 0.50),))
    result = evaluate_gate(current, baseline, thresholds=GateThresholds(max_kappa_drop=0.10))
    assert not result.passed
    assert any(v.check == "judge_kappa:tone_match" for v in result.violations)


def test_judge_regression_on_an_untrusted_dimension_never_blocks() -> None:
    # Baseline itself was already low-confidence (kappa < 0.6) on this dimension -- a further
    # drop still can't fail the gate (tasks/todo.md: "reported but never block").
    baseline = _summary(judge_dimensions=(_dim("contains_unsupported_claim", 0.40),))
    current = _summary(judge_dimensions=(_dim("contains_unsupported_claim", 0.10),))
    result = evaluate_gate(current, baseline)
    assert result.passed


def test_judge_dimension_within_kappa_drop_tolerance_passes() -> None:
    baseline = _summary(judge_dimensions=(_dim("tone_match", 0.80),))
    current = _summary(judge_dimensions=(_dim("tone_match", 0.75),))
    result = evaluate_gate(current, baseline, thresholds=GateThresholds(max_kappa_drop=0.10))
    assert result.passed


def test_multiple_violations_are_all_reported_together() -> None:
    baseline = _summary(total_passed=10, total_cases=10, total_micro_dollars=1000)
    current = _summary(
        total_passed=5,
        total_cases=10,
        total_micro_dollars=5000,
        any_blocks_gate=True,
        cases=(
            CaseSummary(
                case_id="faq-01",
                intent="faq",
                run_id="run_1",
                passed=False,
                blocks_gate=True,
                loop_outcome="completed",
                total_micro_dollars=5000,
                total_tokens=200,
            ),
        ),
    )
    result = evaluate_gate(current, baseline)
    checks = {v.check for v in result.violations}
    assert "required_assertions" in checks
    assert "routing_accuracy" in checks
    assert "total_cost" in checks


def test_format_gate_result_names_baseline_current_and_delta() -> None:
    baseline = _summary(total_micro_dollars=1000)
    current = _summary(total_micro_dollars=2000)
    result = evaluate_gate(
        current, baseline, thresholds=GateThresholds(max_cost_regression_fraction=0.20)
    )
    text = format_gate_result(result)
    assert "total_cost" in text
    assert "1000" in text
    assert "2000" in text
    assert "+100.0%" in text


def test_format_gate_result_on_a_pass_says_so() -> None:
    result = evaluate_gate(_summary(), _summary())
    assert "PASS" in format_gate_result(result)


def test_update_baseline_writes_a_reloadable_summary(tmp_path: Path) -> None:
    summary = _summary()
    baseline_path = tmp_path / "main.json"
    update_baseline(summary, baseline_path)
    reloaded = RunSummary.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    assert reloaded == summary


def test_evaluate_gate_never_writes_the_baseline_file(tmp_path: Path) -> None:
    baseline_path = tmp_path / "main.json"
    update_baseline(_summary(), baseline_path)
    before = baseline_path.read_text(encoding="utf-8")
    evaluate_gate(_summary(total_micro_dollars=99999), _summary())
    after = baseline_path.read_text(encoding="utf-8")
    assert before == after
