Written for: anyone deciding how much to trust TripWire's LLM-judge scores.

# Judge calibration

SPEC.md requires the judge's scores to be checked against human labels, not trusted on their own
— an LLM judge that scores itself is not a measurement. `uv run tripwire calibrate` computes
that check: for every labeled run, it re-scores the run's transcript with the judge and compares
the result to the human label, per rubric dimension (`reply_helpfulness`, `tone_match`,
`escalation_appropriate`, `contains_unsupported_claim`), split into `dev` and `holdout`. The
holdout figure is the headline — `dev` is what the rubric prompt was iterated against, so
agreement there is optimistic by construction.

## Status: not yet run

**No report exists yet.** `tripwire calibrate` requires labeled runs (`data/labels/human.jsonl`,
via `tripwire label`), which requires recorded runs, which requires judge cassettes — all
currently blocked by the same constraint documented in `docs/known-gaps.md`: the Gemini
free-tier daily quota (20 requests/day/model) hasn't allowed enough cassette recording yet to
produce 60 golden-set runs, let alone the 120 labeled runs the Phase 3 checkpoint calls for.

This file states what is *measured*, not what is predicted — `docs/known-gaps.md` is where
predictions belong. There is nothing to predict here: agreement between a judge and a human
rater isn't something that can be reasoned about in advance the way a routing failure can. So
this file stays empty of numbers until a real `tripwire calibrate` run produces them.

## What will go here once real labels exist

- The full per-dimension table `tripwire calibrate` prints: n, raw agreement, kappa (Cohen's for
  the two binary dimensions, quadratic-weighted for the two 1-5 dimensions), a bootstrap 95% CI,
  and the confidence verdict (kappa below 0.6 is low-confidence), for both `dev` and `holdout`.
- Which dimensions came out low-confidence, published as-is — a weak dimension is a fact about
  the judge, not something to hide or re-run until it looks better (SPEC.md § Boundaries).
- The run id and commit each figure traces back to, per the project's "never fabricate a metric"
  rule: every number in this file must be reproducible from a committed `runs/calibration.json`.

## How to produce it

Once enough labels exist:

```
uv run tripwire calibrate --labels data/labels/human.jsonl
```

This writes `runs/calibration.json` and prints the same report to the console. This file gets
rewritten from that output, not from a fresh run described only in prose.
