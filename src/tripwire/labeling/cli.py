"""The interactive labeling flow: show a run's thread + transcript, prompt for the rubric,
append to the label store. See tasks/todo.md Task 13.

The prompt/confirm/echo functions are injected rather than called directly (`typer.prompt` etc.)
so this can be tested without real stdin — a fake that returns a scripted answer sequence
exercises the exact same code path a human's keyboard would.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tripwire.core.records import Span, ToolCallSpan
from tripwire.core.store import TraceReader, default_trace_path
from tripwire.data import load_corpus
from tripwire.judge import render_thread, render_transcript
from tripwire.judge.labels import DEFAULT_SPLIT_SEED, HumanLabel, LabelStore, SplitStore
from tripwire.judge.rubric import RUBRIC_VERSION


def thread_id_from_spans(spans: list[Span]) -> str | None:
    """Every golden case's first tool call is `get_thread(thread_id=...)` by convention, but
    this doesn't assume that specifically — it takes the first `thread_id` argument found on any
    tool call, so a run doesn't need a matching `GoldenCase` to be labelable at all."""
    for span in spans:
        if isinstance(span, ToolCallSpan) and isinstance(span.arguments.get("thread_id"), str):
            return str(span.arguments["thread_id"])
    return None


def label_one_run(
    *,
    run_id: str,
    runs_dir: Path,
    corpus_dir: Path,
    labels_path: Path,
    split_path: Path,
    labeler: str,
    rubric_version: str = RUBRIC_VERSION,
    prompt_int: Callable[[str], int],
    prompt_text: Callable[[str], str],
    confirm: Callable[[str], bool],
    echo: Callable[[str], None],
) -> HumanLabel | None:
    """Label one run interactively. Returns `None` (and prompts nothing) if `run_id` is already
    in the label store — resumable: re-running a labeling session skips what's already done,
    and nothing is written until every dimension has been answered, so an interrupted session
    (Ctrl-C mid-run) loses at most the run in progress, never a previously-saved one.
    """
    store = LabelStore(labels_path)
    if run_id in store.labeled_run_ids():
        echo(f"run {run_id} is already labeled — skipping")
        return None

    trace_path = default_trace_path(run_id, runs_dir)
    _run, spans = TraceReader.load(trace_path)
    thread_id = thread_id_from_spans(spans)

    if thread_id is not None:
        corpus = load_corpus(corpus_dir)
        echo(render_thread(corpus, thread_id))
    else:
        echo("[could not determine thread_id from this trace's tool calls]")
    echo("")
    echo("--- agent transcript ---")
    echo(render_transcript(spans))
    echo("")

    reply_helpfulness = prompt_int("reply_helpfulness (1-5)")
    reply_helpfulness_rationale = prompt_text("  rationale")
    tone_match = prompt_int("tone_match (1-5)")
    tone_match_rationale = prompt_text("  rationale")
    escalation_appropriate = confirm("escalation_appropriate?")
    escalation_appropriate_rationale = prompt_text("  rationale")
    contains_unsupported_claim = confirm("contains_unsupported_claim?")
    contains_unsupported_claim_rationale = prompt_text("  rationale")

    label = HumanLabel(
        run_id=run_id,
        labeler=labeler,
        labeled_at=datetime.now(UTC),
        rubric_version=rubric_version,
        reply_helpfulness=reply_helpfulness,
        reply_helpfulness_rationale=reply_helpfulness_rationale,
        tone_match=tone_match,
        tone_match_rationale=tone_match_rationale,
        escalation_appropriate=escalation_appropriate,
        escalation_appropriate_rationale=escalation_appropriate_rationale,
        contains_unsupported_claim=contains_unsupported_claim,
        contains_unsupported_claim_rationale=contains_unsupported_claim_rationale,
    )
    store.append(label)

    split_store = SplitStore(split_path, seed=DEFAULT_SPLIT_SEED)
    split = split_store.get_or_assign(run_id)
    echo(f"saved — {run_id} assigned to split={split}")
    return label
