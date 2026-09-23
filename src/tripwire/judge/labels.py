"""The human label store and the dev/holdout split. See tasks/todo.md Task 13.

Split assignment is a pure function of `(run_id, seed)` — not a sequential draw from an RNG —
so a run's split never depends on what order runs happen to get labeled in, across sessions or
labelers. Once written to `split.json`, an assignment is never recomputed (SPEC-synthetic-data.md
convention: a stored fact wins over a fresh derivation of the same fact).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Split = Literal["dev", "holdout"]

DEFAULT_SPLIT_SEED = 1337
DEFAULT_HOLDOUT_FRACTION = 0.5


class HumanLabel(BaseModel):
    """One person's rubric scoring of one run — the same four dimensions the LLM judge answers
    (`tripwire.judge.rubric.RubricScore`), plus who labeled it and when."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    labeler: str
    labeled_at: datetime
    rubric_version: str

    reply_helpfulness: int = Field(ge=1, le=5)
    reply_helpfulness_rationale: str
    tone_match: int = Field(ge=1, le=5)
    tone_match_rationale: str
    escalation_appropriate: bool
    escalation_appropriate_rationale: str
    contains_unsupported_claim: bool
    contains_unsupported_claim_rationale: str


class LabelStore:
    """Append-only JSONL store at `data/labels/human.jsonl`. Never rewrites or reorders existing
    lines — `append` is the only write path."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_all(self) -> list[HumanLabel]:
        if not self._path.exists():
            return []
        labels = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                labels.append(HumanLabel.model_validate_json(line))
        return labels

    def labeled_run_ids(self) -> set[str]:
        return {label.run_id for label in self.load_all()}

    def append(self, label: HumanLabel) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(label.model_dump_json() + "\n")


def _deterministic_split(run_id: str, seed: int, holdout_fraction: float) -> Split:
    """A pure function of `(run_id, seed)`: the same run always gets the same split from a fresh
    computation, but `SplitStore` never actually recomputes an already-recorded one — this is the
    fallback used only the first time a run_id is seen."""
    digest = hashlib.sha256(f"{seed}:{run_id}".encode()).hexdigest()
    fraction = int(digest[:8], 16) / 0xFFFFFFFF
    return "holdout" if fraction < holdout_fraction else "dev"


class SplitStore:
    """`data/labels/split.json`: a permanent record of which split each labeled run belongs to.

    `get_or_assign` is idempotent — calling it twice for the same `run_id` returns the same
    split, read from disk the second time, not recomputed (tasks/todo.md Task 13: "generated
    once from a fixed seed and never regenerated silently").
    """

    def __init__(
        self,
        path: Path,
        *,
        seed: int = DEFAULT_SPLIT_SEED,
        holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    ) -> None:
        self._path = path
        self._seed = seed
        self._holdout_fraction = holdout_fraction
        self._assignments: dict[str, Split] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self._seed = data.get("seed", seed)
            self._holdout_fraction = data.get("holdout_fraction", holdout_fraction)
            self._assignments = dict(data.get("assignments", {}))

    def get(self, run_id: str) -> Split | None:
        return self._assignments.get(run_id)

    def get_or_assign(self, run_id: str) -> Split:
        existing = self._assignments.get(run_id)
        if existing is not None:
            return existing
        assigned = _deterministic_split(run_id, self._seed, self._holdout_fraction)
        self._assignments[run_id] = assigned
        self._save()
        return assigned

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "seed": self._seed,
            "holdout_fraction": self._holdout_fraction,
            "assignments": dict(sorted(self._assignments.items())),
        }
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
