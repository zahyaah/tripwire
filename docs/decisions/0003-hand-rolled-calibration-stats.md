# ADR-0003: Hand-written calibration statistics, no scikit-learn

## Status
Accepted

## Date
2026-09-23

## Context
The judge's calibration report (`tripwire calibrate`) needs raw agreement, Cohen's kappa (the two
binary rubric dimensions), quadratic-weighted kappa (the two 1-5 dimensions), a confusion matrix,
and a bootstrap 95% confidence interval. Every one of these is a well-known, well-defined
statistic that scikit-learn already implements (`cohen_kappa_score`, `confusion_matrix`, and a
bootstrap via `scipy.stats.bootstrap` or similar).

TripWire's own core claim is that its numbers are real and checkable, not asserted — SPEC.md § Code
Style: "a kappa implementation tested only against itself is worthless." That standard applies
just as much to a dependency's implementation as to a hand-written one; the difference is whether
the reviewer can actually read the ~15 lines that compute the number.

## Decision
`src/tripwire/judge/stats.py` implements `raw_agreement`, `confusion_matrix`, `cohens_kappa`,
`quadratic_weighted_kappa`, and `bootstrap_ci` as plain functions over `Sequence[object]`/
`Sequence[int]`, with no scientific-computing dependency. Every function is tested against
hand-computed values on small fixed tables, including the degenerate all-agree and all-disagree
cases (`tests/unit/test_stats.py`).

## Alternatives Considered

### scikit-learn
- Pros: battle-tested, handles edge cases the author might miss, one `pip install` away.
- Cons: (1) SPEC.md § Boundaries flags "anything that pulls a scientific stack" as an "ask first"
  dependency — scikit-learn's own dependency tree (numpy, scipy) is exactly that; (2) a portfolio
  harness whose entire premise is "read the code, don't trust the report" is weaker, not stronger,
  if its one statistical core is an opaque import; (3) `scipy.stats.bootstrap`'s exact
  resampling/interval convention would need to be learned and matched anyway to keep this
  project's own "deterministic under a fixed seed" requirement (tasks/todo.md Task 14) — reading
  scipy's source to reproduce that behavior is not meaningfully less work than writing the ~30
  lines `bootstrap_ci` turned out to need.
- Rejected: per SPEC.md Tech Stack, decided before Task 14 was implemented, not reconsidered
  after.

### numpy only (for the percentile/array math, without full scikit-learn)
- Pros: lighter than scikit-learn, still removes some hand-written arithmetic.
- Cons: still a new dependency requiring the same "ask first" conversation; the amount of
  hand-written code numpy would actually remove here is small (a handful of sums and one sort),
  well within what a reviewer can verify by eye against the standard kappa formula.
- Rejected for the same reason as scikit-learn: SPEC.md's dependency policy makes this an
  explicit ask, and the win is marginal.

## Consequences
- ~150 lines of arithmetic in `stats.py` are this project's own code to maintain and get right —
  the trade this ADR makes deliberately, in exchange for every number being traceable to
  reviewable source rather than an imported black box.
- The bootstrap CI's exact convention (percentile method, paired-index resampling, a fixed
  `random.Random(seed)`) is this project's own choice, documented in `bootstrap_ci`'s docstring,
  and does not need to match any particular library's default — there is no default to diverge
  from.
- Adding scikit-learn later (if hand-written coverage ever proves insufficient for a dimension
  this project doesn't yet compute) is still available — this ADR governs the four statistics
  above as implemented, not a permanent ban.
