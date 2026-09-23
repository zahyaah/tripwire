"""Calibration statistics: kappa and weighted kappa against hand-computed values, including the
degenerate all-agree/all-disagree cases, and a deterministic bootstrap CI (tasks/todo.md Task 14).
"""

from __future__ import annotations

import pytest

from tripwire.judge.stats import (
    bootstrap_ci,
    cohens_kappa,
    confusion_matrix,
    quadratic_weighted_kappa,
    raw_agreement,
)

# --- raw_agreement ---------------------------------------------------------------------------


def test_raw_agreement_hand_computed() -> None:
    a = [1, 1, 0, 0, 1]
    b = [1, 0, 0, 0, 1]
    assert raw_agreement(a, b) == pytest.approx(4 / 5)


def test_raw_agreement_empty_is_zero() -> None:
    assert raw_agreement([], []) == 0.0


def test_raw_agreement_mismatched_lengths_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        raw_agreement([1, 2], [1])


# --- confusion_matrix ------------------------------------------------------------------------


def test_confusion_matrix_hand_computed() -> None:
    a = [1, 1, 0, 0]
    b = [1, 0, 0, 0]
    matrix = confusion_matrix(a, b, categories=[0, 1])
    assert matrix == {0: {0: 2, 1: 0}, 1: {0: 1, 1: 1}}


def test_confusion_matrix_includes_unseen_categories_as_zero() -> None:
    matrix = confusion_matrix([1, 1], [1, 1], categories=[0, 1])
    assert matrix[0] == {0: 0, 1: 0}


# --- cohens_kappa (binary) -------------------------------------------------------------------


def test_cohens_kappa_hand_computed() -> None:
    # p_o = 4/5 = 0.8 ; p_e = (3/5*2/5) + (2/5*3/5) = 12/25 = 0.48
    # kappa = (0.8 - 0.48) / (1 - 0.48) = 0.32 / 0.52 = 8/13
    a = [1, 1, 0, 0, 1]
    b = [1, 0, 0, 0, 1]
    assert cohens_kappa(a, b) == pytest.approx(8 / 13)


def test_cohens_kappa_all_agree_is_one() -> None:
    a = [1, 1, 0, 0, 1]
    assert cohens_kappa(a, list(a)) == pytest.approx(1.0)


def test_cohens_kappa_complete_inversion_is_negative_one() -> None:
    # p_o = 0 ; freq_a={1:2,0:2}, freq_b={0:2,1:2} ; p_e = 0.25+0.25 = 0.5
    # kappa = (0 - 0.5) / (1 - 0.5) = -1.0
    a = [1, 1, 0, 0]
    b = [0, 0, 1, 1]
    assert cohens_kappa(a, b) == pytest.approx(-1.0)


def test_cohens_kappa_degenerate_single_category_both_raters_is_one() -> None:
    # Every rating is the same single category for both raters — p_e collapses to 1.0, the
    # zero-denominator case. Convention: report 1.0 (trivially perfect, nothing to disagree on).
    a = [1, 1, 1]
    b = [1, 1, 1]
    assert cohens_kappa(a, b) == pytest.approx(1.0)


def test_cohens_kappa_empty_is_zero() -> None:
    assert cohens_kappa([], []) == 0.0


# --- quadratic_weighted_kappa (1-5, but tested on a smaller 1-3 scale for hand verification) ------


def test_quadratic_weighted_kappa_hand_computed() -> None:
    # See tests/unit/test_stats.py's development notes: hand-derived to weighted_observed=1.5,
    # weighted_expected=2.0 -> kappa = 1 - 1.5/2.0 = 0.25.
    a = [1, 1, 2, 2, 3, 3]
    b = [1, 2, 2, 3, 3, 1]
    kappa = quadratic_weighted_kappa(a, b, min_rating=1, max_rating=3)
    assert kappa == pytest.approx(0.25)


def test_quadratic_weighted_kappa_all_agree_is_one() -> None:
    a = [1, 2, 3, 1, 2, 3]
    assert quadratic_weighted_kappa(a, list(a), min_rating=1, max_rating=3) == pytest.approx(1.0)


def test_quadratic_weighted_kappa_worst_case_is_negative() -> None:
    # Every pair is at the maximum possible distance (1 vs 5) with balanced, non-degenerate
    # marginals on both sides — the case unweighted analysis would call "all disagree".
    a = [1, 1, 5, 5]
    b = [5, 5, 1, 1]
    kappa = quadratic_weighted_kappa(a, b, min_rating=1, max_rating=5)
    assert kappa < 0


def test_quadratic_weighted_kappa_near_miss_scores_higher_than_far_miss() -> None:
    # A rater off by one point should agree "more" (in weighted-kappa terms) than a rater at the
    # opposite end of the scale — that's the entire reason to use quadratic weights on 1-5 data.
    # Hand-computed: true=[3,3,1,1] against near=[4,4,1,1] gives kappa=6/7 (~0.857); against
    # far=[5,5,1,1] gives kappa=2/3 (~0.667). Both marginals are non-degenerate on the true side
    # (has both 1 and 3), so this isn't the zero-variance trivial case.
    true_ratings = [3, 3, 1, 1]
    near_miss = [4, 4, 1, 1]
    far_miss = [5, 5, 1, 1]
    kappa_near = quadratic_weighted_kappa(true_ratings, near_miss, min_rating=1, max_rating=5)
    kappa_far = quadratic_weighted_kappa(true_ratings, far_miss, min_rating=1, max_rating=5)
    assert kappa_near == pytest.approx(6 / 7)
    assert kappa_far == pytest.approx(2 / 3)
    assert kappa_near > kappa_far


def test_quadratic_weighted_kappa_single_category_is_one() -> None:
    assert quadratic_weighted_kappa([3, 3], [3, 3], min_rating=1, max_rating=5) == pytest.approx(
        1.0
    )


def test_quadratic_weighted_kappa_empty_is_one() -> None:
    assert quadratic_weighted_kappa([], [], min_rating=1, max_rating=5) == 1.0


# --- bootstrap_ci -----------------------------------------------------------------------------


def test_bootstrap_ci_is_deterministic_under_a_fixed_seed() -> None:
    a = [1, 1, 0, 0, 1, 0, 1, 1, 0, 0]
    b = [1, 0, 0, 0, 1, 0, 1, 0, 0, 1]
    ci_1 = bootstrap_ci(a, b, cohens_kappa, seed=42, n_resamples=200)
    ci_2 = bootstrap_ci(a, b, cohens_kappa, seed=42, n_resamples=200)
    assert ci_1 == ci_2


def test_bootstrap_ci_bounds_are_ordered_and_contain_the_point_estimate_region() -> None:
    a = [1, 1, 0, 0, 1, 0, 1, 1, 0, 0]
    b = [1, 0, 0, 0, 1, 0, 1, 0, 0, 1]
    lower, upper = bootstrap_ci(a, b, cohens_kappa, seed=1, n_resamples=500)
    assert lower <= upper


def test_bootstrap_ci_single_point_is_degenerate() -> None:
    ci = bootstrap_ci([1], [1], cohens_kappa, seed=1)
    assert ci == (1.0, 1.0)


def test_bootstrap_ci_empty_is_degenerate() -> None:
    ci = bootstrap_ci([], [], cohens_kappa, seed=1)
    assert ci == (0.0, 0.0)
