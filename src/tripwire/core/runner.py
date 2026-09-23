"""Generic, agent-agnostic run lifecycle helpers: hashing a prompt, and building the start/finish
`RunRecord` for one run.

Deliberately knows nothing about inbox triage or any other specific agent — that's what keeps
`trace-core` reusable for a second agent later (CAPABILITY-MAP.md's stated reason `agent` depends
on `trace-core` and never the other way around). The actual agent loop lives under
`agents/<agent_name>/loop.py`.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from tripwire.core.records import RunRecord


def prompt_hash(system_prompt: str) -> str:
    """A stable hash of the system prompt, recorded on every run (SPEC.md: "record the model id,
    effort, prompt hash, and harness version in every run record")."""
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def start_run(
    *,
    run_id: str,
    agent_name: str,
    model: str,
    system_prompt: str,
    harness_version: str,
    llm_mode: Literal["live", "record", "replay"],
    suite_run_id: str | None = None,
    case_id: str | None = None,
) -> RunRecord:
    """The initial `RunRecord`, written before the first model call so a crashed run still
    leaves a trace saying what agent/model/prompt it was running (SPEC-trace-core.md: "A
    crashed run's partial trace is still readable")."""
    return RunRecord(
        run_id=run_id,
        suite_run_id=suite_run_id,
        case_id=case_id,
        agent_name=agent_name,
        model=model,
        prompt_hash=prompt_hash(system_prompt),
        harness_version=harness_version,
        llm_mode=llm_mode,
        started_at=datetime.now(UTC),
    )


def finish_run(
    run: RunRecord,
    *,
    outcome: Literal["completed", "budget_exceeded", "error"],
    error: str | None = None,
) -> RunRecord:
    """The final `RunRecord`, written again (a second "run" line) once the loop ends.

    `TraceWriter`/`TraceReader` don't need an in-place rewrite for this: `TraceReader.load`
    keeps whichever "run" record it read last, so writing the header once at the start and once
    at the end — both through the same append-only `TraceWriter` — gives crash-safety (the start
    record survives a crash) and a complete final record (the common case) without any special
    file-mutation machinery.
    """
    return run.model_copy(
        update={"finished_at": datetime.now(UTC), "outcome": outcome, "error": error}
    )
