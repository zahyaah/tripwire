"""Calibration report: dev/holdout split, per-dimension kappa, and the low-confidence verdict
(tasks/todo.md Task 14)."""

from __future__ import annotations

from datetime import UTC, datetime

from tripwire.judge.calibration import ALL_DIMENSIONS, compute_calibration
from tripwire.judge.labels import HumanLabel
from tripwire.judge.rubric import RubricScore


def _human(run_id: str, **overrides: object) -> HumanLabel:
    base = {
        "run_id": run_id,
        "labeler": "tester@example.com",
        "labeled_at": datetime.now(UTC),
        "rubric_version": "v1",
        "reply_helpfulness": 4,
        "reply_helpfulness_rationale": "ok",
        "tone_match": 4,
        "tone_match_rationale": "ok",
        "escalation_appropriate": True,
        "escalation_appropriate_rationale": "ok",
        "contains_unsupported_claim": False,
        "contains_unsupported_claim_rationale": "ok",
    }
    base.update(overrides)
    return HumanLabel(**base)  # type: ignore[arg-type]


def _judge(**overrides: object) -> RubricScore:
    base = {
        "reply_helpfulness": 4,
        "reply_helpfulness_rationale": "ok",
        "tone_match": 4,
        "tone_match_rationale": "ok",
        "escalation_appropriate": True,
        "escalation_appropriate_rationale": "ok",
        "contains_unsupported_claim": False,
        "contains_unsupported_claim_rationale": "ok",
    }
    base.update(overrides)
    return RubricScore(**base)  # type: ignore[arg-type]


def test_perfect_agreement_is_confident_on_every_dimension() -> None:
    pairs = [(_human(f"run_{i}"), _judge(), "holdout") for i in range(5)]
    report = compute_calibration(pairs)
    holdout = report.for_split("holdout")
    assert {d.dimension for d in holdout} == set(ALL_DIMENSIONS)
    for d in holdout:
        assert d.n == 5
        assert d.raw_agreement == 1.0
        assert d.kappa == 1.0
        assert d.confidence == "confident"


def test_dev_and_holdout_are_reported_separately() -> None:
    pairs = [
        (_human("run_dev_1"), _judge(), "dev"),
        (_human("run_dev_2"), _judge(), "dev"),
        (_human("run_hold_1"), _judge(), "holdout"),
    ]
    report = compute_calibration(pairs)
    dev_n = {d.dimension: d.n for d in report.for_split("dev")}
    holdout_n = {d.dimension: d.n for d in report.for_split("holdout")}
    assert all(n == 2 for n in dev_n.values())
    assert all(n == 1 for n in holdout_n.values())


def test_missing_split_still_gets_a_zero_n_row_not_an_omission() -> None:
    pairs = [(_human("run_dev_1"), _judge(), "dev")]
    report = compute_calibration(pairs)
    holdout = report.for_split("holdout")
    assert len(holdout) == len(ALL_DIMENSIONS)
    assert all(d.n == 0 and d.confidence == "low_confidence" for d in holdout)


def test_low_kappa_dimension_is_flagged_low_confidence() -> None:
    # Humans and judge disagree on tone_match on every single run (1 vs 5, as far apart as a
    # 1-5 scale allows) while agreeing everywhere else — only tone_match should come out
    # low-confidence.
    pairs = [
        (
            _human(f"run_{i}", tone_match=1 if i % 2 == 0 else 5),
            _judge(tone_match=5 if i % 2 == 0 else 1),
            "holdout",
        )
        for i in range(6)
    ]
    report = compute_calibration(pairs)
    by_dimension = {d.dimension: d for d in report.for_split("holdout")}
    assert by_dimension["tone_match"].confidence == "low_confidence"
    assert by_dimension["reply_helpfulness"].confidence == "confident"


def test_kappa_threshold_is_configurable() -> None:
    pairs = [(_human(f"run_{i}"), _judge(), "holdout") for i in range(3)]
    report = compute_calibration(pairs, kappa_threshold=1.1)
    assert all(d.confidence == "low_confidence" for d in report.for_split("holdout"))
