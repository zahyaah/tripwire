"""Golden case schema and loader. See SPEC-synthetic-data.md § Golden set.

A golden case says what a triage run on one thread should do: which tools get called, in what
order, with what arguments, what's forbidden, and what the final state should be. `assertions`
(Task 9/10) evaluates a recorded run against a case; this module only defines the shape and
loads it — no evaluation logic lives here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tripwire.data.models import Intent

MatcherKind = Literal["exact", "subset", "regex"]

Action = Literal["replied", "escalated", "snoozed", "archived", "no_action"]


class GoldenCaseError(Exception):
    """A golden case file failed to load or validate. The message always names the file and,
    where determinable, the case id and offending field(s) — SPEC-synthetic-data.md: "Loader
    errors name the file, the case id, and the offending field."""


class ArgMatcher(BaseModel):
    """How one expected tool argument is checked. `subset` is the default for free-text and
    nested arguments (SPEC-synthetic-data.md); `exact` and `regex` are opt-in per argument."""

    model_config = ConfigDict(extra="forbid")

    matcher: MatcherKind = "subset"
    value: Any


class ExpectedToolCall(BaseModel):
    """One call the golden case expects — a tool name plus a matcher per argument it cares
    about. An argument not listed here is unconstrained (neither required nor forbidden)."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    args: dict[str, ArgMatcher] = Field(default_factory=dict)


class ExpectedOutcome(BaseModel):
    """The state a run should end in. `action` is derived by the assertion runner (Task 9) from
    the agent's actual tool calls and final thread status, not read off a single field — there is
    no "replied" status on `ThreadRuntimeState` (Task 6); a reply is `draft_reply` having been
    called while the thread stayed open."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    action: Action | None = None


class Budgets(BaseModel):
    """Per-case overrides of the loop's default budgets (agents/inbox_triage/loop.py's
    `LoopConfig`)."""

    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=8, ge=1)
    max_micro_dollars: int = Field(default=40_000, ge=0)


class GoldenCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str


class GoldenCase(BaseModel):
    """One golden case, as it appears (or as one entry in a list appears) in a YAML file under
    `data/golden/`."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    intent: Intent
    adversarial: bool = False
    input: GoldenCaseInput
    expected_tool_calls: list[ExpectedToolCall] = Field(default_factory=list)
    order: Literal["subsequence", "strict"] = "subsequence"
    forbidden_tools: list[str] = Field(default_factory=list)
    max_calls: dict[str, int] = Field(default_factory=dict)
    expected_outcome: ExpectedOutcome | None = None
    budgets: Budgets = Field(default_factory=Budgets)
    required: bool = True

    @model_validator(mode="after")
    def _at_least_one_assertion(self) -> GoldenCase:
        has_assertion = bool(
            self.expected_tool_calls or self.forbidden_tools or self.max_calls
        ) or self.expected_outcome is not None
        if not has_assertion:
            raise ValueError(
                "case declares no assertion at all (no expected_tool_calls, forbidden_tools, "
                "max_calls, or expected_outcome) — a case with nothing to check can't fail, "
                "which defeats the point of a golden case (SPEC-synthetic-data.md § Golden set)"
            )
        return self


def _case_id_hint(raw_item: object) -> str:
    if isinstance(raw_item, dict):
        case_id = raw_item.get("case_id")
        if isinstance(case_id, str):
            return case_id
    return "<unknown>"


def _format_validation_error(exc: ValidationError) -> str:
    fields = ", ".join(".".join(str(part) for part in err["loc"]) for err in exc.errors())
    return f"field(s) [{fields}]: {exc}"


def load_golden_file(path: Path) -> list[GoldenCase]:
    """Load every case in one YAML file — either a single case (a top-level mapping) or a list
    of cases (a top-level sequence, the convention for one file per intent)."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise GoldenCaseError(f"{path}: invalid YAML: {exc}") from exc

    if raw is None:
        raise GoldenCaseError(f"{path}: file is empty")
    items = raw if isinstance(raw, list) else [raw]

    cases: list[GoldenCase] = []
    for item in items:
        case_id_hint = _case_id_hint(item)
        try:
            cases.append(GoldenCase.model_validate(item))
        except ValidationError as exc:
            raise GoldenCaseError(
                f"{path}: case {case_id_hint!r} failed validation on "
                f"{_format_validation_error(exc)}"
            ) from exc
    return cases


def load_golden_set(directory: Path) -> list[GoldenCase]:
    """Load every `*.yaml` file under `directory`, sorted by filename for a deterministic
    order, checking for duplicate case ids across files (case ids are permanent and unique —
    SPEC-synthetic-data.md § Golden set)."""
    cases: list[GoldenCase] = []
    seen: dict[str, Path] = {}
    for path in sorted(directory.glob("*.yaml")):
        for case in load_golden_file(path):
            if case.case_id in seen:
                raise GoldenCaseError(
                    f"{path}: duplicate case_id {case.case_id!r} "
                    f"(already defined in {seen[case.case_id]})"
                )
            seen[case.case_id] = path
            cases.append(case)
    return cases
