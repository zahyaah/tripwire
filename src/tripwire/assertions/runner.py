"""Run a golden case end to end and evaluate it against its expectations.

Orchestrates the pieces from earlier tasks — `run_agent` (Task 7), the matcher library
(Task 9) — into the one function both the CLI and the regression suite call.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict

from agents.inbox_triage.loop import LoopConfig, run_agent
from agents.inbox_triage.state import InboxState
from tripwire.assertions.matchers import (
    match_cost_budget,
    match_final_outcome,
    match_max_calls,
    match_not_called,
    match_order,
    match_step_budget,
    match_tool_args,
    match_tool_called,
)
from tripwire.assertions.results import StepAssertionResult
from tripwire.core.golden import GoldenCase
from tripwire.core.ids import new_run_id
from tripwire.core.records import Span
from tripwire.core.store import TraceReader, TraceWriter, default_trace_path
from tripwire.cost.prices import PriceEntry
from tripwire.cost.rollup import (
    DuplicateSpanError,
    IncompleteRunError,
    SpanRunMismatchError,
    rollup,
)
from tripwire.data.models import Corpus
from tripwire.llm.cassettes import CassetteStore
from tripwire.llm.gateway import ModelGateway


class CaseResult(BaseModel):
    """One golden case's outcome. Written to `runs/<run_id>/results.json` alongside the trace
    (tasks/todo.md Task 10: "results are written alongside the trace" — a sibling file, not a
    new span kind; adding a span kind would be a `trace-core` schema change, which is "ask
    first" per SPEC-trace-core.md § Boundaries, and this isn't a trace-core concern at all)."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    run_id: str
    passed: bool
    blocks_gate: bool
    loop_outcome: Literal["completed", "budget_exceeded", "error"]
    loop_error: str | None
    assertion_results: tuple[StepAssertionResult, ...]
    total_micro_dollars: int
    total_tokens: int


def evaluate_case(case: GoldenCase, spans: list[Span]) -> list[StepAssertionResult]:
    """Run every matcher this case's YAML asks for, against a recorded run's spans.

    `required` on every produced result is `case.required` — the golden case schema (Task 8)
    only supports required/advisory at the case level, not per assertion; this is the one place
    that fact turns into behavior.
    """
    results: list[StepAssertionResult] = []
    required = case.required

    for expected_call in case.expected_tool_calls:
        results.append(
            match_tool_called(spans, case.case_id, expected_call.tool, required=required)
        )
        if expected_call.args:
            results.append(
                match_tool_args(
                    spans, case.case_id, expected_call.tool, expected_call.args, required=required
                )
            )

    if case.expected_tool_calls:
        results.append(
            match_order(
                spans,
                case.case_id,
                [c.tool for c in case.expected_tool_calls],
                mode=case.order,
                required=required,
            )
        )

    for forbidden_tool in case.forbidden_tools:
        results.append(match_not_called(spans, case.case_id, forbidden_tool, required=required))

    for tool_name, max_count in case.max_calls.items():
        results.append(
            match_max_calls(spans, case.case_id, tool_name, max_count, required=required)
        )

    if case.expected_outcome is not None:
        results.append(
            match_final_outcome(spans, case.case_id, case.expected_outcome, required=required)
        )

    results.append(
        match_step_budget(spans, case.case_id, case.budgets.max_steps, required=required)
    )
    results.append(
        match_cost_budget(spans, case.case_id, case.budgets.max_micro_dollars, required=required)
    )
    return results


def _cost_and_tokens(run_id: str, spans: list[Span]) -> tuple[int, int]:
    try:
        rolled = rollup(run_id, spans)
    except (IncompleteRunError, SpanRunMismatchError, DuplicateSpanError):
        return 0, 0
    return rolled.total_micro_dollars, rolled.total_tokens


def run_case(
    case: GoldenCase,
    *,
    corpus: Corpus,
    mode: Literal["live", "record", "replay"],
    model: str,
    cassettes_dir: Path,
    runs_dir: Path = Path("runs"),
    client: OpenAI | None = None,
    price_table: Mapping[str, PriceEntry] | None = None,
    suite_run_id: str | None = None,
    prompt_variant: str | None = None,
) -> CaseResult:
    """Execute `case` end to end and evaluate it. A fresh `run_id` every call (never the case
    id itself) — the case id is permanent and one case is run many times over its life; each
    execution gets its own trace file under `runs/`."""
    run_id = new_run_id()
    trace_path = default_trace_path(run_id, runs_dir)
    state = InboxState(corpus)

    with TraceWriter(trace_path) as writer:
        gateway = ModelGateway(
            run_id=run_id,
            writer=writer,
            mode=mode,
            client=client,
            cassettes=CassetteStore(base_dir=cassettes_dir),
            price_table=price_table,
        )
        loop_result = run_agent(
            thread_id=case.input.thread_id,
            state=state,
            gateway=gateway,
            run_id=run_id,
            writer=writer,
            config=LoopConfig(
                model=model,
                max_steps=case.budgets.max_steps,
                max_micro_dollars=case.budgets.max_micro_dollars,
            ),
            case_id=case.case_id,
            suite_run_id=suite_run_id,
            prompt_variant=prompt_variant,
        )

    _run_record, spans = TraceReader.load(trace_path)
    assertion_results = evaluate_case(case, spans)
    total_micro_dollars, total_tokens = _cost_and_tokens(run_id, spans)

    result = CaseResult(
        case_id=case.case_id,
        run_id=run_id,
        passed=all(r.passed for r in assertion_results),
        blocks_gate=any(r.blocks_build for r in assertion_results),
        loop_outcome=loop_result.outcome,
        loop_error=loop_result.error,
        assertion_results=tuple(assertion_results),
        total_micro_dollars=total_micro_dollars,
        total_tokens=total_tokens,
    )
    results_path = trace_path.with_name("results.json")
    results_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
