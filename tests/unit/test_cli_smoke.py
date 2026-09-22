"""Task 1 acceptance: CLI exists, lists every planned subcommand, and every stub fails loudly.

`gen-corpus` graduated from stub to real implementation in Task 5 — it stays in
PLANNED_COMMANDS (the help-listing check) but moves out of STILL_STUB_COMMANDS.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tripwire.cli import app

runner = CliRunner()

PLANNED_COMMANDS = ["run", "report", "label", "calibrate", "gate", "gen-corpus"]
STILL_STUB_COMMANDS = ["run", "report", "label", "calibrate", "gate"]


def test_help_lists_every_planned_subcommand() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in PLANNED_COMMANDS:
        assert command in result.output, f"{command!r} missing from `tripwire --help` output"


def test_bare_run_exits_nonzero_with_stub_message() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 1
    assert "not implemented" in result.output


def test_every_remaining_stub_exits_nonzero() -> None:
    for command in STILL_STUB_COMMANDS:
        # `label` requires --run; give it a placeholder so the stub message is what's tested.
        args = [command] if command != "label" else [command, "--run", "placeholder"]
        result = runner.invoke(app, args)
        assert result.exit_code == 1, f"`tripwire {command}` did not exit non-zero"
        assert "not implemented" in result.output


def test_gen_corpus_writes_to_an_explicit_out_dir_not_the_real_repo_data_dir(
    tmp_path: Path,
) -> None:
    # --out-dir exists specifically so a test run never writes into the real data/corpus/ — see
    # SPEC.md Testing Strategy: "tests must not write into data/ or fixtures/".
    out_dir = tmp_path / "corpus"
    result = runner.invoke(
        app, ["gen-corpus", "--seed", "1", "--threads", "5", "--out-dir", str(out_dir)]
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "threads.json").exists()
