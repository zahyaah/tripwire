"""Suite-level run summary: per-case verdicts, routing accuracy, judge agreement, cost, and a
suite-wide p95 step latency — one JSON and one Markdown rendering of the same data. See
tasks/todo.md Task 16.

Every figure here is summed or selected from a `CaseResult` (assertions.run_case) or a
`RunRollup` (cost.rollup), both already computed from real spans — nothing in this module
estimates anything (SPEC.md's "never fabricate a metric," reapplied at the suite level).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tripwire.assertions.runner import CaseResult
from tripwire.cost.rollup import RunRollup, p95_nearest_rank
from tripwire.judge.calibration import DimensionReport


class CaseSummary(BaseModel):
    """One case's verdict, cost, and tokens — the per-case row of the suite table."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    intent: str
    run_id: str
    passed: bool
    blocks_gate: bool
    loop_outcome: Literal["completed", "budget_exceeded", "error"]
    total_micro_dollars: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class IntentAccuracy(BaseModel):
    """Routing accuracy for one intent: `passed / total` cases of that intent."""

    model_config = ConfigDict(frozen=True)

    intent: str
    passed: int = Field(ge=0)
    total: int = Field(ge=1)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total


class RunSummary(BaseModel):
    """The full suite summary: everything `tripwire run` and the regression gate need, and
    everything a PR comment should show."""

    model_config = ConfigDict(frozen=True)

    suite_run_id: str
    model: str
    llm_mode: Literal["live", "record", "replay"]
    prompt_hash: str
    started_at: datetime
    cases: tuple[CaseSummary, ...]
    per_intent: tuple[IntentAccuracy, ...]
    total_cases: int = Field(ge=0)
    total_passed: int = Field(ge=0)
    total_micro_dollars: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    p95_step_latency_ms: int = Field(ge=0)
    judge_dimensions: tuple[DimensionReport, ...] = ()
    any_blocks_gate: bool

    @property
    def overall_accuracy(self) -> float | None:
        return self.total_passed / self.total_cases if self.total_cases else None


def build_summary(
    *,
    suite_run_id: str,
    model: str,
    llm_mode: Literal["live", "record", "replay"],
    prompt_hash: str,
    started_at: datetime,
    case_intents: Mapping[str, str],
    case_results: Sequence[CaseResult],
    rollups: Mapping[str, RunRollup],
    judge_dimensions: Sequence[DimensionReport] = (),
) -> RunSummary:
    """Assemble a `RunSummary` from already-computed per-case results.

    `rollups` is keyed by `run_id` (`CaseResult.run_id`), not `case_id` — a case run more than
    once across the suite's history has a fresh `run_id` each time (`run_case`'s own docstring),
    so `run_id` is the only key that unambiguously picks the rollup for *this* execution. A
    missing entry (a case whose rollup couldn't be computed — an incomplete trace, say)
    contributes zero step latencies to the pooled p95 rather than raising: one bad case shouldn't
    block the whole suite's summary from being written.
    """
    cases: list[CaseSummary] = []
    intent_totals: dict[str, int] = {}
    intent_passed: dict[str, int] = {}
    pooled_step_latencies: list[int] = []
    total_micro_dollars = 0
    total_tokens = 0

    for result in case_results:
        intent = case_intents.get(result.case_id, "unknown")
        cases.append(
            CaseSummary(
                case_id=result.case_id,
                intent=intent,
                run_id=result.run_id,
                passed=result.passed,
                blocks_gate=result.blocks_gate,
                loop_outcome=result.loop_outcome,
                total_micro_dollars=result.total_micro_dollars,
                total_tokens=result.total_tokens,
            )
        )
        intent_totals[intent] = intent_totals.get(intent, 0) + 1
        if result.passed:
            intent_passed[intent] = intent_passed.get(intent, 0) + 1
        total_micro_dollars += result.total_micro_dollars
        total_tokens += result.total_tokens

        rolled = rollups.get(result.run_id)
        if rolled is not None:
            pooled_step_latencies.extend(step.latency_ms for step in rolled.step_costs)

    per_intent = tuple(
        IntentAccuracy(intent=intent, passed=intent_passed.get(intent, 0), total=total)
        for intent, total in sorted(intent_totals.items())
    )

    return RunSummary(
        suite_run_id=suite_run_id,
        model=model,
        llm_mode=llm_mode,
        prompt_hash=prompt_hash,
        started_at=started_at,
        cases=tuple(cases),
        per_intent=per_intent,
        total_cases=len(case_results),
        total_passed=sum(1 for r in case_results if r.passed),
        total_micro_dollars=total_micro_dollars,
        total_tokens=total_tokens,
        p95_step_latency_ms=p95_nearest_rank(pooled_step_latencies),
        judge_dimensions=tuple(judge_dimensions),
        any_blocks_gate=any(r.blocks_gate for r in case_results),
    )


def render_markdown(summary: RunSummary) -> str:
    """A Markdown table suitable for pasting directly into a PR comment (tasks/todo.md Task 16
    manual check) — the same rendering `tripwire report` writes to `summary.md` and CI posts."""
    lines = [
        f"## TripWire run `{summary.suite_run_id}`",
        "",
        f"- model: `{summary.model}` (mode: `{summary.llm_mode}`, "
        f"prompt_hash: `{summary.prompt_hash[:12]}...`)",
        f"- started: {summary.started_at.isoformat()}",
        f"- overall accuracy: "
        f"{_format_accuracy(summary.overall_accuracy)} "
        f"({summary.total_passed}/{summary.total_cases})",
        f"- total cost: {summary.total_micro_dollars} µ$",
        f"- total tokens: {summary.total_tokens}",
        f"- p95 step latency: {summary.p95_step_latency_ms} ms",
        f"- gate: {'**FAILING**' if summary.any_blocks_gate else 'passing'}",
        "",
        "### Per-intent accuracy",
        "",
        "| intent | passed/total | accuracy |",
        "|---|---|---|",
    ]
    for row in summary.per_intent:
        lines.append(f"| {row.intent} | {row.passed}/{row.total} | {row.accuracy:.0%} |")

    lines += [
        "",
        "### Cases",
        "",
        "| case | intent | outcome | passed | cost (µ$) | run_id |",
        "|---|---|---|---|---|---|",
    ]
    for case in summary.cases:
        mark = "✅" if case.passed else "❌"
        lines.append(
            f"| {case.case_id} | {case.intent} | {case.loop_outcome} | {mark} | "
            f"{case.total_micro_dollars} | `{case.run_id}` |"
        )

    if summary.judge_dimensions:
        lines += [
            "",
            "### Judge agreement (holdout)",
            "",
            "| dimension | n | agreement | kappa | confidence |",
            "|---|---|---|---|---|",
        ]
        for dim in summary.judge_dimensions:
            if dim.split != "holdout":
                continue
            lines.append(
                f"| {dim.dimension} | {dim.n} | {dim.raw_agreement:.2f} | "
                f"{dim.kappa:.2f} | {dim.confidence} |"
            )

    return "\n".join(lines) + "\n"


def _format_accuracy(accuracy: float | None) -> str:
    return f"{accuracy:.0%}" if accuracy is not None else "n/a"


def write_summary(summary: RunSummary, out_dir: Path) -> tuple[Path, Path]:
    """Write `summary.json` and `summary.md` into `out_dir` (conventionally
    `runs/<suite_run_id>/`). Returns the two paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "summary.json"
    md_path = out_dir / "summary.md"
    json_path.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    return json_path, md_path
