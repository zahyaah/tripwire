"""Compare judge scores to human labels and report measured agreement per dimension, split by
dev/holdout. See tasks/todo.md Task 14.

`compute_calibration` is a pure function over already-gathered `(human, judge, split)` triples —
it does not call the judge itself. Gathering a judge score for each labeled run (via
`tripwire.judge.judge.judge_run`, which needs a live or cassetted model call) is the caller's
job, kept out of this module so the statistics themselves stay testable with plain fixtures and
no gateway involved at all.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tripwire.judge.labels import HumanLabel, Split
from tripwire.judge.rubric import RubricScore
from tripwire.judge.stats import bootstrap_ci, cohens_kappa, quadratic_weighted_kappa, raw_agreement

KAPPA_CONFIDENCE_THRESHOLD = 0.6

BINARY_DIMENSIONS: tuple[str, ...] = ("escalation_appropriate", "contains_unsupported_claim")
ORDINAL_DIMENSIONS: tuple[str, ...] = ("reply_helpfulness", "tone_match")
ALL_DIMENSIONS: tuple[str, ...] = ORDINAL_DIMENSIONS + BINARY_DIMENSIONS


class DimensionReport(BaseModel):
    """One rubric dimension's agreement, for one split."""

    model_config = ConfigDict(frozen=True)

    dimension: str
    split: Split
    n: int = Field(ge=0)
    raw_agreement: float
    kappa: float
    kappa_ci_lower: float
    kappa_ci_upper: float
    confidence: Literal["confident", "low_confidence"]


class CalibrationReport(BaseModel):
    """The full report: every dimension, both splits."""

    model_config = ConfigDict(frozen=True)

    dimensions: tuple[DimensionReport, ...]

    def for_split(self, split: Split) -> tuple[DimensionReport, ...]:
        return tuple(d for d in self.dimensions if d.split == split)


LabelJudgeSplit = tuple[HumanLabel, RubricScore, Split]


def compute_calibration(
    pairs: list[LabelJudgeSplit],
    *,
    kappa_threshold: float = KAPPA_CONFIDENCE_THRESHOLD,
    n_resamples: int = 1000,
    seed: int = 1337,
) -> CalibrationReport:
    """Compute per-dimension, per-split agreement between human labels and judge scores.

    A dimension/split with zero labeled runs still gets a row (n=0, low_confidence) rather than
    being silently omitted — a missing row in a calibration report reads as "not measured yet",
    which is worse than a visible zero (SPEC.md's "never fabricate a metric" cuts both ways: an
    absent row can imply more confidence than an explicit "n=0" one).
    """
    reports: list[DimensionReport] = []
    for split in ("dev", "holdout"):
        split_pairs = [p for p in pairs if p[2] == split]
        for dimension in ORDINAL_DIMENSIONS:
            reports.append(
                _dimension_report(
                    dimension,
                    split,
                    split_pairs,
                    is_ordinal=True,
                    kappa_threshold=kappa_threshold,
                    n_resamples=n_resamples,
                    seed=seed,
                )
            )
        for dimension in BINARY_DIMENSIONS:
            reports.append(
                _dimension_report(
                    dimension,
                    split,
                    split_pairs,
                    is_ordinal=False,
                    kappa_threshold=kappa_threshold,
                    n_resamples=n_resamples,
                    seed=seed,
                )
            )
    return CalibrationReport(dimensions=tuple(reports))


def _dimension_report(
    dimension: str,
    split: Split,
    split_pairs: list[LabelJudgeSplit],
    *,
    is_ordinal: bool,
    kappa_threshold: float,
    n_resamples: int,
    seed: int,
) -> DimensionReport:
    human_values = [getattr(human, dimension) for human, _judge, _split in split_pairs]
    judge_values = [getattr(judge, dimension) for _human, judge, _split in split_pairs]
    n = len(split_pairs)

    if n == 0:
        return DimensionReport(
            dimension=dimension,
            split=split,
            n=0,
            raw_agreement=0.0,
            kappa=0.0,
            kappa_ci_lower=0.0,
            kappa_ci_upper=0.0,
            confidence="low_confidence",
        )

    def kappa_fn(a: list[object], b: list[object]) -> float:
        if is_ordinal:
            return quadratic_weighted_kappa(a, b, min_rating=1, max_rating=5)  # type: ignore[arg-type]
        return cohens_kappa(a, b)

    agreement = raw_agreement(human_values, judge_values)
    kappa = kappa_fn(human_values, judge_values)
    lower, upper = bootstrap_ci(
        human_values, judge_values, kappa_fn, n_resamples=n_resamples, seed=seed  # type: ignore[arg-type]
    )
    confidence: Literal["confident", "low_confidence"] = (
        "confident" if kappa >= kappa_threshold else "low_confidence"
    )
    return DimensionReport(
        dimension=dimension,
        split=split,
        n=n,
        raw_agreement=agreement,
        kappa=kappa,
        kappa_ci_lower=lower,
        kappa_ci_upper=upper,
        confidence=confidence,
    )


def format_report_text(report: CalibrationReport) -> str:
    """A plain-text table, for the CLI and for pasting into `docs/judge-calibration.md`."""
    lines = [
        f"{'dimension':<28} {'split':<8} {'n':>4} {'agree':>7} {'kappa':>7} "
        f"{'95% CI':>16} {'confidence':>15}"
    ]
    for d in report.dimensions:
        ci = f"[{d.kappa_ci_lower:.2f}, {d.kappa_ci_upper:.2f}]"
        lines.append(
            f"{d.dimension:<28} {d.split:<8} {d.n:>4} {d.raw_agreement:>7.2f} "
            f"{d.kappa:>7.2f} {ci:>16} {d.confidence:>15}"
        )
    return "\n".join(lines)
