"""Task 1 acceptance: CLI exists, lists every planned subcommand, and every stub fails loudly.

`gen-corpus` (Task 5), `run` (Task 10), `label` (Task 13), and `calibrate` (Task 14) graduated
from stub to real implementation — they stay in PLANNED_COMMANDS (the help-listing check) but
move out of STILL_STUB_COMMANDS.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from tripwire.cli import app

_REPO_ROOT = Path(__file__).resolve().parents[2]

runner = CliRunner()

PLANNED_COMMANDS = ["run", "report", "label", "calibrate", "gate", "gen-corpus"]
STILL_STUB_COMMANDS: list[str] = []


def test_help_lists_every_planned_subcommand() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in PLANNED_COMMANDS:
        assert command in result.output, f"{command!r} missing from `tripwire --help` output"


def test_every_remaining_stub_exits_nonzero() -> None:
    for command in STILL_STUB_COMMANDS:
        result = runner.invoke(app, [command])
        assert result.exit_code == 1, f"`tripwire {command}` did not exit non-zero"
        assert "not implemented" in result.output


def test_run_actually_works_as_the_real_installed_console_script(tmp_path: Path) -> None:
    # Regression: CliRunner.invoke() runs in-process and inherits this test session's own
    # sys.path — which pytest already populated with the repo root via pyproject.toml's
    # `pythonpath = ["."]`. That made every other `run` test in this file pass even when the
    # real installed console script (`uv run tripwire ...`) raised
    # `ModuleNotFoundError: No module named 'agents'`. Invoked from a directory that is NOT the
    # repo root, so a cwd-based sys.path fluke can't accidentally make this pass either — the
    # fix under test must be the explicit sys.path insertion keyed off `_repo_root()`.
    suite_dir = tmp_path / "golden"
    suite_dir.mkdir()
    (suite_dir / "one.yaml").write_text(
        "case_id: subprocess-smoke-01\n"
        "intent: faq\n"
        "input:\n"
        "  thread_id: thr_00001\n"
        "expected_tool_calls:\n"
        "  - tool: get_thread\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(_REPO_ROOT),
            "tripwire",
            "run",
            "--suite",
            str(suite_dir),
            "--mode",
            "replay",
        ],
        cwd=tmp_path,  # NOT the repo root — --project is what points uv at the right env
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert result.returncode == 1  # no cassette recorded for this made-up case -> expected fail
    assert "subprocess-smoke-01" in result.stdout


def test_gate_actually_works_as_the_real_installed_console_script(tmp_path: Path) -> None:
    # Regression: `tripwire.report` (imported by the `gate` command) transitively imports
    # `tripwire.assertions.runner`, which imports `agents.inbox_triage.loop` — the same
    # ModuleNotFoundError class `run`'s own subprocess test above guards against, caught here for
    # `gate` specifically because `gate`'s sys.path fix was originally missing entirely (found by
    # actually running `uv run tripwire gate` by hand, not by this in-process test suite, which
    # inherits pytest's own pythonpath and would never have seen the failure).
    result = subprocess.run(
        ["uv", "run", "--project", str(_REPO_ROOT), "tripwire", "gate", "--run", "does-not-exist"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert result.returncode == 2
    assert "no summary" in result.stdout or "no summary" in result.stderr


def test_label_missing_run_fails_clearly(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["label", "--run", "run_does_not_exist", "--labeler", "tester@example.com"]
    )
    assert result.exit_code == 2
    assert "run_does_not_exist" in result.output


def test_gate_on_a_run_id_with_no_summary_fails_clearly() -> None:
    result = runner.invoke(app, ["gate", "--run", "suite_does_not_exist"])
    assert result.exit_code == 2
    assert "no summary" in result.output


def test_report_on_a_run_id_with_no_trace_fails_clearly() -> None:
    result = runner.invoke(app, ["report", "--run", "run_does_not_exist"])
    assert result.exit_code == 2
    assert "no trace" in result.output


def test_calibrate_with_no_labels_fails_clearly(tmp_path: Path) -> None:
    result = runner.invoke(app, ["calibrate", "--labels", str(tmp_path / "does_not_exist.jsonl")])
    assert result.exit_code == 1
    assert "nothing to calibrate" in result.output


def test_run_rejects_an_invalid_mode() -> None:
    result = runner.invoke(app, ["run", "--mode", "not-a-real-mode"])
    assert result.exit_code == 2
    assert "must be one of live, record, replay" in result.output


def test_run_in_replay_mode_with_no_cassette_fails_clearly_not_silently(
    tmp_path: Path,
) -> None:
    # No API key needed for this: replay mode never touches the network. The point of this test
    # is that a missing cassette surfaces as a named, readable failure — not a crash, and not a
    # silently-passing case with nothing actually checked.
    suite_dir = tmp_path / "golden"
    suite_dir.mkdir()
    (suite_dir / "one.yaml").write_text(
        "case_id: cli-smoke-no-cassette-01\n"
        "intent: faq\n"
        "input:\n"
        "  thread_id: thr_00001\n"
        "expected_tool_calls:\n"
        "  - tool: get_thread\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["run", "--suite", str(suite_dir), "--mode", "replay"])
    assert result.exit_code == 1
    assert "cli-smoke-no-cassette-01" in result.output
    assert "run did not complete" in result.output or "cassette" in result.output.lower()


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
