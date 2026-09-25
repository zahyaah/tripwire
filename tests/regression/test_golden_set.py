"""The regression suite: one parametrized test per golden case, in replay mode.

tasks/todo.md Task 10: "parametrizes over all cases with the case id as the pytest parameter id
and fails on any required assertion failure." Runs from committed cassettes only — no network,
no API key (SPEC.md § Assumptions #2). A case whose cassette hasn't been recorded yet fails with
`CassetteMissError`, which is the correct, honest failure: this suite does not fabricate a
result for a case it cannot actually replay.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tripwire.assertions import run_case
from tripwire.core.golden import GoldenCase, load_golden_set
from tripwire.data import load_corpus

pytestmark = pytest.mark.regression

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN_DIR = _REPO_ROOT / "data" / "golden"
_CORPUS_DIR = _REPO_ROOT / "data" / "corpus"
_CASSETTES_DIR = _REPO_ROOT / "fixtures" / "cassettes"

_CASES = load_golden_set(_GOLDEN_DIR)


@pytest.mark.parametrize("case", _CASES, ids=[c.case_id for c in _CASES])
def test_golden_case(case: GoldenCase, tmp_path: Path) -> None:
    corpus = load_corpus(_CORPUS_DIR)
    result = run_case(
        case,
        corpus=corpus,
        mode="replay",
        model="gemini-3.1-flash-lite",
        cassettes_dir=_CASSETTES_DIR,
        runs_dir=tmp_path,  # regression runs don't need to keep their trace around
    )
    failing = [a.detail for a in result.assertion_results if a.blocks_build]
    if result.loop_outcome != "completed":
        # Surface the loop's own failure (e.g. a missing cassette) ahead of the assertion
        # mismatches it causes — those are downstream noise once the run itself didn't finish,
        # and burying the real cause behind eight generic "no matching call" lines is exactly
        # the unhelpful-failure-message problem tasks/todo.md Task 10 asks this suite to avoid.
        failing.insert(
            0,
            f"[{case.case_id}] run did not complete: outcome={result.loop_outcome!r} "
            f"error={result.loop_error!r}",
        )
    assert not failing, "\n".join(failing)
