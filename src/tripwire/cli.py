"""TripWire CLI entry point.

Subcommands are stubbed in Task 1 (repository scaffold) and implemented across the tasks named
in each stub's error message. See tasks/todo.md for the full task list.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import typer

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
    mode: str = typer.Option("replay", help="One of: live, record, replay."),
) -> None:
    """Execute the golden set against the agent under test."""
    _not_implemented("run", "Task 10")


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
) -> None:
    """Interactively label a run's transcript against the judge rubric."""
    _not_implemented("label", "Task 13")


@app.command()
def calibrate(
    labels_path: str = typer.Option("data/labels/human.jsonl", "--labels"),
) -> None:
    """Compute judge-vs-human agreement statistics on the holdout label split."""
    _not_implemented("calibrate", "Task 14")


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
