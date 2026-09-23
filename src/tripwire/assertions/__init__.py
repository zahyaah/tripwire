"""The matcher library: decides whether a recorded run satisfies a golden case's expectations.
Reads only recorded spans — never a live agent object. See SPEC-synthetic-data.md § Golden set
and tasks/todo.md Task 9.
"""

from __future__ import annotations

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
from tripwire.assertions.results import StepAssertionResult, make_result
from tripwire.assertions.runner import CaseResult, evaluate_case, run_case

__all__ = [
    "CaseResult",
    "StepAssertionResult",
    "evaluate_case",
    "make_result",
    "match_cost_budget",
    "match_final_outcome",
    "match_max_calls",
    "match_not_called",
    "match_order",
    "match_step_budget",
    "match_tool_args",
    "match_tool_called",
    "run_case",
]
