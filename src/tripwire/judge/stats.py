"""Hand-written calibration statistics: raw agreement, Cohen's kappa, quadratic-weighted kappa,
a confusion matrix, and a bootstrap confidence interval. No scikit-learn (SPEC.md § Tech Stack):
owning ~100 lines of well-tested arithmetic is what makes the calibration claim this project
makes about itself checkable in code review, not a black-box import.

Every function here is a pure function over two equal-length sequences — no I/O, no model
calls — so it's testable against hand-computed values with nothing to fake.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence

StatFn = Callable[[Sequence[float], Sequence[float]], float]


def raw_agreement(a: Sequence[object], b: Sequence[object]) -> float:
    """Fraction of pairs where `a[i] == b[i]`. Works for bool, int, or any hashable category."""
    _check_same_length(a, b)
    if not a:
        return 0.0
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)


def confusion_matrix(
    a: Sequence[object], b: Sequence[object], categories: Sequence[object]
) -> dict[object, dict[object, int]]:
    """`matrix[true_category][pred_category] = count`, over exactly `categories` (so an unseen
    category still shows up as an all-zero row/column instead of silently vanishing)."""
    _check_same_length(a, b)
    matrix: dict[object, dict[object, int]] = {c: dict.fromkeys(categories, 0) for c in categories}
    for x, y in zip(a, b, strict=True):
        matrix[x][y] += 1
    return matrix


def cohens_kappa(a: Sequence[object], b: Sequence[object]) -> float:
    """Unweighted Cohen's kappa over any number of categories (binary is the common case here,
    per tasks/todo.md Task 14, but the formula itself doesn't care).

    kappa = (p_o - p_e) / (1 - p_e), where p_o is observed agreement and p_e is the agreement
    expected by chance from each rater's own marginal category frequencies.

    Degenerate case: if every rating from both raters is the same single category, p_e = 1 and
    the formula divides by zero. By convention (matching common implementations such as
    scikit-learn's), that's reported as kappa=1.0 — perfect, trivial agreement — since p_o is
    necessarily also 1.0 in that case; there's no other category `1 - p_e` could be measuring
    disagreement against.
    """
    _check_same_length(a, b)
    n = len(a)
    if n == 0:
        return 0.0
    categories = sorted(set(a) | set(b), key=repr)
    freq_a = {c: 0 for c in categories}
    freq_b = {c: 0 for c in categories}
    observed_agree = 0
    for x, y in zip(a, b, strict=True):
        freq_a[x] += 1
        freq_b[y] += 1
        if x == y:
            observed_agree += 1
    p_o = observed_agree / n
    p_e = sum((freq_a[c] / n) * (freq_b[c] / n) for c in categories)
    if p_e >= 1.0:
        return 1.0
    return (p_o - p_e) / (1 - p_e)


def quadratic_weighted_kappa(
    a: Sequence[int], b: Sequence[int], *, min_rating: int, max_rating: int
) -> float:
    """Quadratic-weighted kappa for ordinal ratings (the 1-5 rubric dimensions).

    Standard formula: build the observed confusion matrix `O` and the chance-expected matrix `E`
    (outer product of each rater's marginals, scaled to the same total), weight every cell by
    `w[i][j] = (i-j)^2 / (K-1)^2` where `K` is the number of categories, then
    `kappa = 1 - sum(w*O) / sum(w*E)`. A near-miss (4 rated as a 3) costs less than a wild miss
    (5 rated as a 1) — that's the entire point of using this over unweighted kappa on an ordinal
    scale.
    """
    _check_same_length(a, b)
    n = len(a)
    categories = list(range(min_rating, max_rating + 1))
    k = len(categories)
    if n == 0 or k <= 1:
        return 1.0 if n == 0 or all(x == a[0] for x in a) else 0.0

    index = {c: i for i, c in enumerate(categories)}
    observed = [[0.0] * k for _ in range(k)]
    marg_a = [0.0] * k
    marg_b = [0.0] * k
    for x, y in zip(a, b, strict=True):
        observed[index[x]][index[y]] += 1
        marg_a[index[x]] += 1
        marg_b[index[y]] += 1

    weights = [[((i - j) ** 2) / ((k - 1) ** 2) for j in range(k)] for i in range(k)]
    expected = [[marg_a[i] * marg_b[j] / n for j in range(k)] for i in range(k)]

    weighted_observed = sum(
        weights[i][j] * observed[i][j] for i in range(k) for j in range(k)
    )
    weighted_expected = sum(
        weights[i][j] * expected[i][j] for i in range(k) for j in range(k)
    )
    if weighted_expected == 0:
        return 1.0
    return 1 - weighted_observed / weighted_expected


def bootstrap_ci(
    a: Sequence[float],
    b: Sequence[float],
    statistic: StatFn,
    *,
    n_resamples: int = 1000,
    seed: int = 1337,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """A percentile bootstrap confidence interval for `statistic(a, b)`.

    Deterministic under a fixed seed (tasks/todo.md Task 14 verification bullet): resamples
    *pairs* (the same index from both `a` and `b` together, never `a` and `b` independently —
    breaking the pairing would measure something other than rater agreement), computes the
    statistic on each resample, and reports the percentile interval. `n=0` or `n=1` returns a
    degenerate `(value, value)` interval rather than raising — a CI on one data point is not
    meaningful, but returning something over raising keeps a caller's report from crashing on a
    small holdout split.
    """
    _check_same_length(a, b)
    n = len(a)
    point = statistic(a, b)
    if n <= 1:
        return (point, point)

    rng = random.Random(seed)
    resampled_stats = []
    for _ in range(n_resamples):
        indices = [rng.randrange(n) for _ in range(n)]
        resample_a = [a[i] for i in indices]
        resample_b = [b[i] for i in indices]
        resampled_stats.append(statistic(resample_a, resample_b))

    resampled_stats.sort()
    alpha = 1 - confidence
    lower_idx = max(0, int((alpha / 2) * n_resamples))
    upper_idx = min(n_resamples - 1, int((1 - alpha / 2) * n_resamples) - 1)
    return (resampled_stats[lower_idx], resampled_stats[upper_idx])


def _check_same_length(a: Sequence[object], b: Sequence[object]) -> None:
    if len(a) != len(b):
        raise ValueError(f"a and b must be the same length, got {len(a)} and {len(b)}")
