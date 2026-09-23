"""Persisted judge scores, one entry per time a run was scored.

`JudgeCallSpan` (trace-core) only carries cost, usage, and `rubric_version` — trace-core stays
domain-agnostic (`core/records.py`'s own docstring: "nothing here knows about... scoring"), so
the rubric's actual answer (`reply_helpfulness`, `tone_match`, ...) cannot live on the span
without coupling the generic trace schema to one rubric shape. `judge_run` (judge.py) returns
that answer to its caller and nothing durable keeps it past that call — this store is where it
goes, so a run's judge score survives long enough for `tripwire calibrate` to compute agreement
and for the HTML trace viewer (Task 15) to display it after the fact, reading only a file, same
as everything else in `report`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from tripwire.judge.rubric import RubricScore


class JudgeScore(BaseModel):
    """One judge_run answer for one run."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    rubric_version: str
    score: RubricScore


class JudgeScoreStore:
    """Append-only JSONL at `data/labels/judge_scores.jsonl`.

    A run scored more than once (re-running `tripwire calibrate`) keeps every line rather than
    overwriting — `latest_for_run` takes the last match, mirroring `TraceReader`'s own
    last-record-wins convention for a repeated key, so nothing is silently lost on disk even
    though only the latest is normally read back.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_all(self) -> list[JudgeScore]:
        if not self._path.exists():
            return []
        scores = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                scores.append(JudgeScore.model_validate_json(line))
        return scores

    def latest_for_run(self, run_id: str) -> JudgeScore | None:
        matches = [s for s in self.load_all() if s.run_id == run_id]
        return matches[-1] if matches else None

    def append(self, score: JudgeScore) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(score.model_dump_json() + "\n")
