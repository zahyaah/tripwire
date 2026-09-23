"""TripWire CLI entry point.

Subcommands are stubbed in Task 1 (repository scaffold) and implemented across the tasks named
in each stub's error message. See tasks/todo.md for the full task list.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table

# Provider swap #2 (2026-09-23): NVIDIA -> Gemini. Confirmed live via client.models.list()
# against the real API — the docs' own listed "gemini-3-flash" 404'd; this is the real id.
DEFAULT_MODEL = "gemini-3.8-flash"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

app = typer.Typer(
    name="tripwire",
    help="Evaluation and observability harness for multi-step agents.",
    no_args_is_help=True,
)


def _repo_root() -> Path:
    """Walk up from this file until a `pyproject.toml` is found.

    CassetteStore and the corpus writer default to cwd-relative paths by design (see
    SPEC-trace-core.md § Cassettes and the Task 4 doubt-cycle note) — it's this entry point's
    job to anchor them at the repo root, so `tripwire gen-corpus` behaves the same run from any
    subdirectory instead of silently writing `data/corpus/` wherever the shell happened to be.
    """
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("could not find repo root (no pyproject.toml in any parent directory)")


def _git_user_email() -> str | None:
    """Best-effort labeler identity from `git config user.email`. Returns `None` (never raises)
    if git isn't available or nothing is configured — the caller decides what to do about a
    missing identity, this helper just doesn't invent one."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    email = result.stdout.strip()
    return email or None


def _not_implemented(command: str, task: str) -> NoReturn:
    """Fail loudly and name the task that implements this command.

    A stub that silently succeeds is worse than one that errors: it would let a CI job or a
    script believe a command ran when it did nothing.
    """
    typer.secho(
        f"tripwire {command}: not implemented yet (see tasks/todo.md {task})",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=1)


@app.command()
def run(
    suite: str = typer.Option("data/golden", help="Path to the golden set directory."),
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="One of: live, record, replay. Defaults to $TRIPWIRE_LLM_MODE, else 'replay'.",
    ),
    model: str = typer.Option(DEFAULT_MODEL, "--model"),
) -> None:
    """Execute the golden set against the agent under test."""
    import sys

    from openai import OpenAI

    # `agents/` is a top-level directory alongside `src/`, deliberately not part of the
    # installed `tripwire` wheel (SPEC.md Project Structure: the agent under test isn't shipped
    # as part of the harness). Tests get it on sys.path via pyproject.toml's
    # `[tool.pytest.ini_options] pythonpath = ["."]`; the installed console script (this
    # function, run as `uv run tripwire ...`) gets no such treatment from Python itself, so it's
    # done here, once, before the first import that needs it.
    repo_root_str = str(_repo_root())
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    from tripwire.assertions import run_case
    from tripwire.core.golden import GoldenCaseError, load_golden_set
    from tripwire.data import load_corpus

    resolved_mode = mode or os.environ.get("TRIPWIRE_LLM_MODE", "replay")
    if resolved_mode not in ("live", "record", "replay"):
        typer.secho(
            f"--mode must be one of live, record, replay (got {resolved_mode!r})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    repo_root = _repo_root()
    suite_dir = Path(suite)
    if not suite_dir.is_absolute():
        suite_dir = repo_root / suite_dir

    try:
        cases = load_golden_set(suite_dir)
    except GoldenCaseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    try:
        corpus = load_corpus(repo_root / "data" / "corpus")
    except FileNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    client = None
    if resolved_mode in ("live", "record"):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            typer.secho(
                f"--mode {resolved_mode} calls the API and needs GEMINI_API_KEY set",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        client = OpenAI(api_key=api_key, base_url=GEMINI_BASE_URL)

    console = Console()
    table = Table(title=f"tripwire run — {len(cases)} case(s), mode={resolved_mode}")
    table.add_column("case_id")
    table.add_column("outcome")
    table.add_column("passed")
    table.add_column("cost (µ$)", justify="right")
    table.add_column("run_id")

    # Note: run_case never actually raises CassetteMissError/CorruptCassetteError out to this
    # caller — the loop (agents/inbox_triage/loop.py) already catches every exception from
    # gateway.create(), including both of those, and records it as loop_outcome="error" with
    # loop_error set. That's what's checked below, not a try/except around run_case itself.
    any_blocks_gate = False
    intent_totals: dict[str, int] = {}
    intent_passed: dict[str, int] = {}
    for case in cases:
        result = run_case(
            case,
            corpus=corpus,
            mode=resolved_mode,  # type: ignore[arg-type]
            model=model,
            cassettes_dir=repo_root / "fixtures" / "cassettes",
            runs_dir=repo_root / "runs",
            client=client,
        )
        intent_totals[case.intent] = intent_totals.get(case.intent, 0) + 1
        if result.passed:
            intent_passed[case.intent] = intent_passed.get(case.intent, 0) + 1
        table.add_row(
            result.case_id,
            result.loop_outcome,
            "true" if result.passed else "false",
            str(result.total_micro_dollars),
            result.run_id,
            style="red" if result.blocks_gate else None,
        )
        if result.blocks_gate:
            any_blocks_gate = True
            if result.loop_outcome != "completed":
                # Surface the loop's own failure (a missing cassette, a budget, a raw error)
                # ahead of the assertion mismatches it causes — those are downstream noise once
                # the run itself didn't finish. markup=False: case ids and error text can
                # contain "[...]" (e.g. a cassette key error message), which Rich would
                # otherwise silently parse as a style tag and swallow instead of printing.
                console.print(
                    f"  [{result.case_id}] run did not complete: "
                    f"outcome={result.loop_outcome!r} error={result.loop_error!r}",
                    style="red",
                    markup=False,
                )
            for assertion in result.assertion_results:
                if assertion.blocks_build:
                    console.print(f"  {assertion.detail}", style="red", markup=False)

    console.print(table)

    # Routing accuracy: "passed" (every assertion satisfied, required or advisory) is the honest
    # content verdict — distinct from `blocks_gate`, which only reflects required assertions.
    # Reported overall and per intent (tasks/todo.md Task 11 verification).
    total_passed = sum(intent_passed.values())
    total_cases = sum(intent_totals.values())
    accuracy_table = Table(title="routing accuracy")
    accuracy_table.add_column("intent")
    accuracy_table.add_column("passed / total", justify="right")
    accuracy_table.add_column("accuracy", justify="right")
    for intent in sorted(intent_totals):
        passed_n = intent_passed.get(intent, 0)
        total_n = intent_totals[intent]
        accuracy_table.add_row(intent, f"{passed_n}/{total_n}", f"{passed_n / total_n:.0%}")
    accuracy_table.add_row(
        "overall",
        f"{total_passed}/{total_cases}",
        f"{total_passed / total_cases:.0%}" if total_cases else "n/a",
        style="bold",
    )
    console.print(accuracy_table)

    if any_blocks_gate:
        raise typer.Exit(code=1)


@app.command()
def report(
    run_id: str = typer.Option("latest", "--run", help="Run id to report on, or 'latest'."),
    open_: bool = typer.Option(False, "--open", help="Open the HTML trace after generating it."),
) -> None:
    """Generate the HTML trace and run summary for a completed run."""
    _not_implemented("report", "Task 15-16")


@app.command()
def label(
    run_id: str = typer.Option(..., "--run", help="Run id to label."),
    labeler: str = typer.Option(
        None, "--labeler", help="Defaults to $TRIPWIRE_LABELER, else your git user.email."
    ),
) -> None:
    """Interactively label a run's transcript against the judge rubric."""
    from tripwire.labeling import label_one_run

    resolved_labeler = labeler or os.environ.get("TRIPWIRE_LABELER") or _git_user_email()
    if not resolved_labeler:
        typer.secho(
            "no labeler identity found — pass --labeler or set TRIPWIRE_LABELER",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    repo_root = _repo_root()
    try:
        result = label_one_run(
            run_id=run_id,
            runs_dir=repo_root / "runs",
            corpus_dir=repo_root / "data" / "corpus",
            labels_path=repo_root / "data" / "labels" / "human.jsonl",
            split_path=repo_root / "data" / "labels" / "split.json",
            labeler=resolved_labeler,
            prompt_int=lambda msg: typer.prompt(msg, type=int),
            prompt_text=typer.prompt,
            confirm=typer.confirm,
            echo=typer.echo,
        )
    except FileNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    if result is None:
        raise typer.Exit(code=0)


@app.command()
def calibrate(
    labels_path: str = typer.Option("data/labels/human.jsonl", "--labels"),
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="One of: live, record, replay. Defaults to $TRIPWIRE_LLM_MODE, else 'replay'.",
    ),
    model: str = typer.Option(DEFAULT_MODEL, "--model"),
) -> None:
    """Score every labeled run with the judge and report agreement against the human labels."""
    import sys

    from openai import OpenAI

    repo_root_str = str(_repo_root())
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    from tripwire.core.store import TraceReader, TraceWriter, default_trace_path
    from tripwire.data import load_corpus
    from tripwire.judge import JudgeOutputError, JudgeScore, JudgeScoreStore, judge_run
    from tripwire.judge.calibration import compute_calibration, format_report_text
    from tripwire.judge.labels import DEFAULT_SPLIT_SEED, LabelStore, SplitStore
    from tripwire.judge.rubric import RUBRIC_VERSION
    from tripwire.labeling.cli import thread_id_from_spans
    from tripwire.llm.cassettes import CassetteStore
    from tripwire.llm.gateway import ModelGateway

    repo_root = _repo_root()
    resolved_labels_path = Path(labels_path)
    if not resolved_labels_path.is_absolute():
        resolved_labels_path = repo_root / resolved_labels_path

    labels = LabelStore(resolved_labels_path).load_all()
    if not labels:
        typer.secho(
            f"no labels at {resolved_labels_path} — nothing to calibrate (see `tripwire label`)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    resolved_mode = mode or os.environ.get("TRIPWIRE_LLM_MODE", "replay")
    if resolved_mode not in ("live", "record", "replay"):
        typer.secho(
            f"--mode must be one of live, record, replay (got {resolved_mode!r})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    client = None
    if resolved_mode in ("live", "record"):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            typer.secho(
                f"--mode {resolved_mode} calls the API and needs GEMINI_API_KEY set",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        client = OpenAI(api_key=api_key, base_url=GEMINI_BASE_URL)

    corpus = load_corpus(repo_root / "data" / "corpus")
    split_store = SplitStore(
        repo_root / "data" / "labels" / "split.json", seed=DEFAULT_SPLIT_SEED
    )
    cassettes = CassetteStore(base_dir=repo_root / "fixtures" / "cassettes")
    runs_dir = repo_root / "runs"

    pairs: list[tuple[object, object, str]] = []
    skipped: list[str] = []
    for label in labels:
        trace_path = default_trace_path(label.run_id, runs_dir)
        if not trace_path.exists():
            skipped.append(f"{label.run_id} (no trace)")
            continue
        _run, spans = TraceReader.load(trace_path)
        thread_id = thread_id_from_spans(spans)
        if thread_id is None:
            skipped.append(f"{label.run_id} (no thread_id in trace)")
            continue

        writer = TraceWriter(trace_path)
        try:
            gateway = ModelGateway(
                run_id=label.run_id,
                writer=writer,
                mode=resolved_mode,  # type: ignore[arg-type]
                client=client,
                cassettes=cassettes,
            )
            try:
                score = judge_run(
                    gateway=gateway,
                    model=model,
                    thread_id=thread_id,
                    spans=spans,
                    corpus=corpus,
                    step_index=len(spans),
                    parent_span_id=None,
                )
            except JudgeOutputError as exc:
                skipped.append(f"{label.run_id} (judge output error: {exc})")
                continue
        finally:
            writer.close()

        JudgeScoreStore(repo_root / "data" / "labels" / "judge_scores.jsonl").append(
            JudgeScore(run_id=label.run_id, rubric_version=RUBRIC_VERSION, score=score)
        )
        split = split_store.get_or_assign(label.run_id)
        pairs.append((label, score, split))

    if skipped:
        typer.secho(f"skipped {len(skipped)} labeled run(s):", fg=typer.colors.YELLOW, err=True)
        for reason in skipped:
            typer.secho(f"  {reason}", fg=typer.colors.YELLOW, err=True)

    if not pairs:
        typer.secho(
            "no labeled run could be scored by the judge — nothing to report",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    report = compute_calibration(pairs)  # type: ignore[arg-type]
    console = Console()
    console.print(format_report_text(report), markup=False)

    out_path = runs_dir / "calibration.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    typer.echo(f"wrote {out_path}")

    holdout_low_confidence = [
        d.dimension for d in report.for_split("holdout") if d.confidence == "low_confidence"
    ]
    if holdout_low_confidence:
        typer.secho(
            f"low-confidence on holdout (kappa < 0.6): {', '.join(holdout_low_confidence)}",
            fg=typer.colors.YELLOW,
            err=True,
        )


@app.command()
def gate(
    run_id: str = typer.Option("latest", "--run"),
    baseline: str = typer.Option("baselines/main.json", "--baseline"),
) -> None:
    """Compare a run's summary against the committed baseline; exit non-zero on regression."""
    _not_implemented("gate", "Task 17")


@app.command(name="gen-corpus")
def gen_corpus(
    seed: int = typer.Option(1337, "--seed"),
    threads: int = typer.Option(400, "--threads"),
    out_dir: Path | None = typer.Option(
        None, "--out-dir", help="Defaults to <repo root>/data/corpus."
    ),
) -> None:
    """Generate the deterministic synthetic inbox corpus."""
    from tripwire.data import generate_corpus, write_corpus

    resolved_out_dir = out_dir if out_dir is not None else _repo_root() / "data" / "corpus"
    corpus = generate_corpus(seed=seed, thread_count=threads)
    write_corpus(corpus, resolved_out_dir)
    typer.echo(
        f"wrote {corpus.manifest.thread_count} threads, "
        f"{corpus.manifest.customer_count} customers, "
        f"{corpus.manifest.order_count} orders to {resolved_out_dir} "
        f"(content_hash={corpus.manifest.content_hash[:12]}...)"
    )


if __name__ == "__main__":
    app()
