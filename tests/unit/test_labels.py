"""Label store and split assignment (tasks/todo.md Task 13): round-trip, resumable skip, split
stability across invocations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tripwire.judge.labels import HumanLabel, LabelStore, SplitStore


def _label(run_id: str = "run_1") -> HumanLabel:
    return HumanLabel(
        run_id=run_id,
        labeler="tester@example.com",
        labeled_at=datetime.now(UTC),
        rubric_version="v1",
        reply_helpfulness=4,
        reply_helpfulness_rationale="Addressed the ask.",
        tone_match=5,
        tone_match_rationale="Calm and on point.",
        escalation_appropriate=True,
        escalation_appropriate_rationale="No escalation needed, none happened.",
        contains_unsupported_claim=False,
        contains_unsupported_claim_rationale="Nothing unverified was claimed.",
    )


def test_label_round_trip(tmp_path: Path) -> None:
    store = LabelStore(tmp_path / "human.jsonl")
    label = _label()
    store.append(label)
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0] == label


def test_labeled_run_ids_reflects_appended_labels(tmp_path: Path) -> None:
    store = LabelStore(tmp_path / "human.jsonl")
    store.append(_label("run_1"))
    store.append(_label("run_2"))
    assert store.labeled_run_ids() == {"run_1", "run_2"}


def test_missing_label_file_returns_empty_not_an_error(tmp_path: Path) -> None:
    store = LabelStore(tmp_path / "does_not_exist.jsonl")
    assert store.load_all() == []
    assert store.labeled_run_ids() == set()


def test_append_is_additive_not_a_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "human.jsonl"
    store = LabelStore(path)
    store.append(_label("run_1"))
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    store.append(_label("run_2"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == first_line  # untouched by the second append


# --- split assignment -----------------------------------------------------------------------------


def test_split_is_stable_across_invocations(tmp_path: Path) -> None:
    path = tmp_path / "split.json"
    first = SplitStore(path).get_or_assign("run_1")
    second = SplitStore(path).get_or_assign("run_1")  # fresh instance, same file
    assert first == second


def test_split_is_deterministic_for_the_same_seed(tmp_path: Path) -> None:
    a = SplitStore(tmp_path / "a.json", seed=1337).get_or_assign("run_x")
    b = SplitStore(tmp_path / "b.json", seed=1337).get_or_assign("run_x")
    assert a == b


def test_split_assignment_persists_to_disk_immediately(tmp_path: Path) -> None:
    path = tmp_path / "split.json"
    store = SplitStore(path)
    split = store.get_or_assign("run_1")
    assert path.exists()
    reloaded = SplitStore(path)
    assert reloaded.get("run_1") == split


def test_split_is_never_silently_recomputed_even_if_seed_changes(tmp_path: Path) -> None:
    path = tmp_path / "split.json"
    original = SplitStore(path, seed=1337).get_or_assign("run_1")
    # A later call with a *different* seed must still return the already-recorded split, not a
    # freshly computed one under the new seed — "generated once... never regenerated silently".
    same = SplitStore(path, seed=9999).get_or_assign("run_1")
    assert same == original


def test_split_assigns_both_dev_and_holdout_across_many_runs(tmp_path: Path) -> None:
    store = SplitStore(tmp_path / "split.json")
    splits = {store.get_or_assign(f"run_{i}") for i in range(50)}
    assert splits == {"dev", "holdout"}, "expected both splits to appear across 50 runs"


# --- label_one_run: the full interactive flow, with injected fake prompts --------------------


def _write_labelable_trace(trace_path, run_id, thread_id):
    from tripwire.core.records import AgentRunSpan, RunRecord, ToolCallSpan
    from tripwire.core.store import TraceWriter

    with TraceWriter(trace_path) as writer:
        writer.write_run(
            RunRecord(
                run_id=run_id,
                agent_name="inbox_triage",
                model="test-model",
                prompt_hash="deadbeef",
                harness_version="0.1.0",
                llm_mode="replay",
                started_at=datetime.now(UTC),
            )
        )
        writer.append(
            AgentRunSpan(
                span_id="sp_root",
                parent_span_id=None,
                run_id=run_id,
                step_index=0,
                started_at=datetime.now(UTC),
                latency_ms=1,
                step_count=1,
                total_micro_dollars=0,
            )
        )
        writer.append(
            ToolCallSpan(
                span_id="sp_tool",
                parent_span_id="sp_root",
                run_id=run_id,
                step_index=0,
                started_at=datetime.now(UTC),
                latency_ms=1,
                tool_name="get_thread",
                arguments={"thread_id": thread_id},
                result_summary="ok",
                result_bytes=2,
            )
        )


def test_label_one_run_full_flow_writes_label_and_assigns_split(tmp_path: Path) -> None:
    from tripwire.core.store import default_trace_path
    from tripwire.data import generate_corpus, write_corpus
    from tripwire.labeling import label_one_run

    corpus_dir = tmp_path / "corpus"
    write_corpus(generate_corpus(seed=1, thread_count=5), corpus_dir)
    thread_id = generate_corpus(seed=1, thread_count=5).threads[0].thread_id

    runs_dir = tmp_path / "runs"
    trace_path = default_trace_path("run_1", runs_dir)
    _write_labelable_trace(trace_path, "run_1", thread_id)

    answers_int = iter([4, 5])
    answers_text = iter(["helpful reply", "good tone", "no escalation needed", "no bad claims"])
    answers_bool = iter([True, False])
    echoed: list[str] = []

    result = label_one_run(
        run_id="run_1",
        runs_dir=runs_dir,
        corpus_dir=corpus_dir,
        labels_path=tmp_path / "human.jsonl",
        split_path=tmp_path / "split.json",
        labeler="tester@example.com",
        prompt_int=lambda _msg: next(answers_int),
        prompt_text=lambda _msg: next(answers_text),
        confirm=lambda _msg: next(answers_bool),
        echo=echoed.append,
    )

    assert result is not None
    assert result.run_id == "run_1"
    assert result.reply_helpfulness == 4
    assert result.tone_match == 5
    assert result.escalation_appropriate is True
    assert result.contains_unsupported_claim is False
    assert any("saved" in line for line in echoed)

    store = LabelStore(tmp_path / "human.jsonl")
    assert "run_1" in store.labeled_run_ids()
    split_store = SplitStore(tmp_path / "split.json")
    assert split_store.get("run_1") in ("dev", "holdout")


def test_label_one_run_skips_an_already_labeled_run_without_prompting(tmp_path: Path) -> None:
    from tripwire.core.store import default_trace_path
    from tripwire.data import generate_corpus, write_corpus
    from tripwire.labeling import label_one_run

    corpus_dir = tmp_path / "corpus"
    write_corpus(generate_corpus(seed=1, thread_count=5), corpus_dir)
    thread_id = generate_corpus(seed=1, thread_count=5).threads[0].thread_id
    runs_dir = tmp_path / "runs"
    _write_labelable_trace(default_trace_path("run_1", runs_dir), "run_1", thread_id)

    labels_path = tmp_path / "human.jsonl"
    LabelStore(labels_path).append(_label("run_1"))

    def _fail_prompt_int(_msg: str) -> int:
        raise AssertionError("should not prompt for an already-labeled run")

    result = label_one_run(
        run_id="run_1",
        runs_dir=runs_dir,
        corpus_dir=corpus_dir,
        labels_path=labels_path,
        split_path=tmp_path / "split.json",
        labeler="tester@example.com",
        prompt_int=_fail_prompt_int,
        prompt_text=lambda _msg: (_ for _ in ()).throw(AssertionError("no prompt expected")),
        confirm=lambda _msg: (_ for _ in ()).throw(AssertionError("no prompt expected")),
        echo=lambda _msg: None,
    )
    assert result is None
    # still exactly one label on disk — skipping did not duplicate or overwrite anything
    assert len(LabelStore(labels_path).load_all()) == 1
