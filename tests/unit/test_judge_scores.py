"""JudgeScoreStore round-trip and last-write-wins lookup (tasks/todo.md Task 15 groundwork —
see judge/scores.py's docstring for why this store exists)."""

from __future__ import annotations

from pathlib import Path

from tripwire.judge.rubric import RubricScore
from tripwire.judge.scores import JudgeScore, JudgeScoreStore


def _score(run_id: str, reply_helpfulness: int = 4) -> JudgeScore:
    return JudgeScore(
        run_id=run_id,
        rubric_version="v1",
        score=RubricScore(
            reply_helpfulness=reply_helpfulness,
            reply_helpfulness_rationale="addressed the ask",
            tone_match=5,
            tone_match_rationale="calm and on point",
            escalation_appropriate=True,
            escalation_appropriate_rationale="none needed, none happened",
            contains_unsupported_claim=False,
            contains_unsupported_claim_rationale="nothing unverified",
        ),
    )


def test_round_trip(tmp_path: Path) -> None:
    store = JudgeScoreStore(tmp_path / "judge_scores.jsonl")
    score = _score("run_1")
    store.append(score)
    assert store.load_all() == [score]


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    store = JudgeScoreStore(tmp_path / "does_not_exist.jsonl")
    assert store.load_all() == []
    assert store.latest_for_run("run_1") is None


def test_latest_for_run_takes_the_last_match(tmp_path: Path) -> None:
    store = JudgeScoreStore(tmp_path / "judge_scores.jsonl")
    store.append(_score("run_1", reply_helpfulness=2))
    store.append(_score("run_1", reply_helpfulness=5))
    store.append(_score("run_2", reply_helpfulness=3))
    latest = store.latest_for_run("run_1")
    assert latest is not None
    assert latest.score.reply_helpfulness == 5


def test_append_never_overwrites_prior_lines(tmp_path: Path) -> None:
    path = tmp_path / "judge_scores.jsonl"
    store = JudgeScoreStore(path)
    store.append(_score("run_1"))
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    store.append(_score("run_2"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == first_line
