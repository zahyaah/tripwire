"""The result type every matcher returns. See SPEC.md § Code Style for the original sketch of
this shape; this is its full version with the case id, step index, and expected/actual values
the failure-message acceptance criterion (tasks/todo.md Task 9) requires.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StepAssertionResult(BaseModel):
    """The outcome of one matcher call against one golden case.

    `expected`/`actual` are always populated (even on a pass) as short strings a report can show
    side by side — `detail` is the composed, already-readable failure message (empty on pass, so
    a report can treat a nonempty `detail` as "read this").
    """

    model_config = ConfigDict(frozen=True)

    assertion_id: str = Field(description='e.g. "tool_called:get_thread", "order", "cost_budget"')
    case_id: str
    passed: bool
    required: bool = True
    step_index: int | None = Field(
        default=None, description="The step this assertion is about, or None if case-wide"
    )
    expected: str = ""
    actual: str = ""
    detail: str = Field(default="", description="Human-readable reason; empty on pass")

    @property
    def blocks_build(self) -> bool:
        """Matches SPEC.md's original sketch: only a failed *required* assertion blocks."""
        return self.required and not self.passed


def make_result(
    *,
    assertion_id: str,
    case_id: str,
    passed: bool,
    expected: str,
    actual: str,
    required: bool = True,
    step_index: int | None = None,
) -> StepAssertionResult:
    """Build a `StepAssertionResult`, composing `detail` from the other fields on failure.

    Every matcher builds its result through this so the "case id, step index, expected, actual"
    acceptance criterion (tasks/todo.md Task 9) is satisfied in exactly one place, not
    re-implemented per matcher.
    """
    detail = (
        ""
        if passed
        else (
            f"[{case_id}]"
            + (f" step {step_index}" if step_index is not None else "")
            + f" {assertion_id}: expected {expected}, got {actual}"
        )
    )
    return StepAssertionResult(
        assertion_id=assertion_id,
        case_id=case_id,
        passed=passed,
        required=required,
        step_index=step_index,
        expected=expected,
        actual=actual,
        detail=detail,
    )
