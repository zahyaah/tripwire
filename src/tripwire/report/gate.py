"""Compare a run summary against a committed baseline and decide whether the build fails. See
tasks/todo.md Task 17.

Four independent failure modes — any one alone fails the gate:

1. the current run has a failing required assertion (`RunSummary.any_blocks_gate`) — absolute,
   no baseline comparison needed.
2. overall routing accuracy dropped by more than `max_accuracy_drop` versus baseline.
3. total cost or p95 step latency regressed by more than its tolerance versus baseline.
4. a judge dimension the baseline already trusted (holdout kappa at or above
   `confident_kappa_threshold`) dropped by more than `max_kappa_drop`. A dimension the baseline
   never trusted can never regress the gate (tasks/todo.md: "judge dimensions below the kappa
   threshold are reported but never block").

Updating the baseline (`update_baseline`) is a separate, explicit action — evaluating the gate
never writes to the baseline file itself (tasks/todo.md: "never automatic").
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from tripwire.judge.calibration import KAPPA_CONFIDENCE_THRESHOLD
from tripwire.report.summary import RunSummary


class GateThresholds(BaseModel):
    """Every tolerance the gate checks against. Defaults are this project's own choice (not
    sourced from anywhere) — tune them per SPEC.md's Open Questions once real suite runs exist to
    calibrate against."""

    model_config = ConfigDict(frozen=True)

    max_accuracy_drop: float = Field(default=0.05, ge=0)
    max_cost_regression_fraction: float = Field(default=0.20, ge=0)
    max_latency_regression_fraction: float = Field(default=0.20, ge=0)
    max_kappa_drop: float = Field(default=0.10, ge=0)
    confident_kappa_threshold: float = Field(default=KAPPA_CONFIDENCE_THRESHOLD, ge=0, le=1)


DEFAULT_THRESHOLDS = GateThresholds()


class GateViolation(BaseModel):
    """One violated threshold, with baseline, current, and delta already formatted for direct
    display (tasks/todo.md: "gate output names every violated threshold with baseline, current,
    and delta")."""

    model_config = ConfigDict(frozen=True)

    check: str
    baseline: str
    current: str
    delta: str
    detail: str


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    violations: tuple[GateViolation, ...]


def evaluate_gate(
    current: RunSummary,
    baseline: RunSummary,
    *,
    thresholds: GateThresholds = DEFAULT_THRESHOLDS,
) -> GateResult:
    violations: list[GateViolation] = []

    if current.any_blocks_gate:
        failing = [c.case_id for c in current.cases if c.blocks_gate]
        violations.append(
            GateViolation(
                check="required_assertions",
                baseline="0 failing",
                current=f"{len(failing)} failing",
                delta=", ".join(failing),
                detail=f"required assertion(s) failed on: {', '.join(failing)}",
            )
        )

    _check_accuracy(current, baseline, thresholds, violations)
    _check_cost(current, baseline, thresholds, violations)
    _check_latency(current, baseline, thresholds, violations)
    _check_judge(current, baseline, thresholds, violations)

    return GateResult(passed=not violations, violations=tuple(violations))


def _check_accuracy(
    current: RunSummary,
    baseline: RunSummary,
    thresholds: GateThresholds,
    violations: list[GateViolation],
) -> None:
    base_acc = baseline.overall_accuracy
    cur_acc = current.overall_accuracy
    if base_acc is None or cur_acc is None:
        return
    drop = base_acc - cur_acc
    if drop > thresholds.max_accuracy_drop:
        violations.append(
            GateViolation(
                check="routing_accuracy",
                baseline=f"{base_acc:.1%}",
                current=f"{cur_acc:.1%}",
                delta=f"-{drop:.1%}",
                detail=(
                    f"routing accuracy dropped {drop:.1%}, exceeding tolerance "
                    f"{thresholds.max_accuracy_drop:.1%}"
                ),
            )
        )


def _check_cost(
    current: RunSummary,
    baseline: RunSummary,
    thresholds: GateThresholds,
    violations: list[GateViolation],
) -> None:
    if baseline.total_micro_dollars <= 0:
        return  # no percentage basis to compare against (SPEC.md: never fabricate a metric)
    fraction = (
        current.total_micro_dollars - baseline.total_micro_dollars
    ) / baseline.total_micro_dollars
    if fraction > thresholds.max_cost_regression_fraction:
        violations.append(
            GateViolation(
                check="total_cost",
                baseline=f"{baseline.total_micro_dollars} µ$",
                current=f"{current.total_micro_dollars} µ$",
                delta=f"+{fraction:.1%}",
                detail=(
                    f"total cost regressed {fraction:.1%}, exceeding tolerance "
                    f"{thresholds.max_cost_regression_fraction:.1%}"
                ),
            )
        )


def _check_latency(
    current: RunSummary,
    baseline: RunSummary,
    thresholds: GateThresholds,
    violations: list[GateViolation],
) -> None:
    if baseline.p95_step_latency_ms <= 0:
        return
    fraction = (
        current.p95_step_latency_ms - baseline.p95_step_latency_ms
    ) / baseline.p95_step_latency_ms
    if fraction > thresholds.max_latency_regression_fraction:
        violations.append(
            GateViolation(
                check="p95_step_latency",
                baseline=f"{baseline.p95_step_latency_ms} ms",
                current=f"{current.p95_step_latency_ms} ms",
                delta=f"+{fraction:.1%}",
                detail=(
                    f"p95 step latency regressed {fraction:.1%}, exceeding tolerance "
                    f"{thresholds.max_latency_regression_fraction:.1%}"
                ),
            )
        )


def _check_judge(
    current: RunSummary,
    baseline: RunSummary,
    thresholds: GateThresholds,
    violations: list[GateViolation],
) -> None:
    current_by_key = {(d.dimension, d.split): d for d in current.judge_dimensions}
    for base_dim in baseline.judge_dimensions:
        if base_dim.split != "holdout":
            continue
        if base_dim.kappa < thresholds.confident_kappa_threshold:
            continue  # baseline never trusted this dimension — it can't regress the gate
        cur_dim = current_by_key.get((base_dim.dimension, "holdout"))
        if cur_dim is None:
            continue  # nothing to compare this run — not a violation, just not measured here
        drop = base_dim.kappa - cur_dim.kappa
        if drop > thresholds.max_kappa_drop:
            violations.append(
                GateViolation(
                    check=f"judge_kappa:{base_dim.dimension}",
                    baseline=f"{base_dim.kappa:.2f}",
                    current=f"{cur_dim.kappa:.2f}",
                    delta=f"-{drop:.2f}",
                    detail=(
                        f"{base_dim.dimension} holdout kappa dropped {drop:.2f} (baseline "
                        f"trusted it at >= {thresholds.confident_kappa_threshold:.2f})"
                    ),
                )
            )


def format_gate_result(result: GateResult) -> str:
    if result.passed:
        return "gate: PASS — no threshold violated"
    lines = [f"gate: FAIL — {len(result.violations)} violation(s)", ""]
    for v in result.violations:
        lines.append(f"  [{v.check}] baseline={v.baseline} current={v.current} delta={v.delta}")
        lines.append(f"    {v.detail}")
    return "\n".join(lines)


def update_baseline(summary: RunSummary, baseline_path: Path) -> None:
    """Overwrite the baseline file with `summary`. The only place a baseline is ever written —
    `evaluate_gate` never calls this itself."""
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
