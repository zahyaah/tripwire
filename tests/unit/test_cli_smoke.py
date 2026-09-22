"""Task 1 acceptance: CLI exists, lists every planned subcommand, and every stub fails loudly."""

from __future__ import annotations

from typer.testing import CliRunner

from tripwire.cli import app

runner = CliRunner()

PLANNED_COMMANDS = ["run", "report", "label", "calibrate", "gate", "gen-corpus"]


def test_help_lists_every_planned_subcommand() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in PLANNED_COMMANDS:
        assert command in result.output, f"{command!r} missing from `tripwire --help` output"


def test_bare_run_exits_nonzero_with_stub_message() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 1
    assert "not implemented" in result.output


def test_every_stub_exits_nonzero() -> None:
    for command in PLANNED_COMMANDS:
        # `label` requires --run; give it a placeholder so the stub message is what's tested.
        args = [command] if command != "label" else [command, "--run", "placeholder"]
        result = runner.invoke(app, args)
        assert result.exit_code == 1, f"`tripwire {command}` did not exit non-zero"
        assert "not implemented" in result.output
