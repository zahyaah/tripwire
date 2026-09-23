"""The matcher library: pure functions from recorded spans to a `StepAssertionResult`.

Every matcher here takes `spans: list[Span]` (plus whatever the specific check needs) and reads
only those recorded trace records — never an `InboxState`, never a live agent object
(tasks/todo.md Task 9 acceptance criterion, and CAPABILITY-MAP.md's stated reason `assertions`
can evaluate a run without importing `agents` at all).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from tripwire.assertions.results import StepAssertionResult, make_result
from tripwire.core.golden import ArgMatcher, ExpectedOutcome
from tripwire.core.records import AgentRunSpan, Span, ToolCallSpan
from tripwire.cost.rollup import (
    DuplicateSpanError,
    IncompleteRunError,
    SpanRunMismatchError,
    rollup,
)

# A tool call in this set is what changes a thread's final disposition. `final_outcome`'s
# `action` is derived from the *last successful* call to one of these, not read off any single
# field — there is no "replied" status on ThreadRuntimeState (Task 6); a reply is `draft_reply`
# having been called while the thread stayed open.
_ACTION_TOOLS: dict[str, str] = {
    "escalate": "escalated",
    "snooze": "snoozed",
    "archive": "archived",
    "draft_reply": "replied",
}


def _tool_calls(spans: list[Span], tool_name: str) -> list[ToolCallSpan]:
    return [s for s in spans if isinstance(s, ToolCallSpan) and s.tool_name == tool_name]


def _arg_matches(actual: Any, matcher: ArgMatcher) -> bool:
    if matcher.matcher == "regex":
        return re.search(str(matcher.value), "" if actual is None else str(actual)) is not None
    if matcher.matcher == "exact":
        return bool(actual == matcher.value)
    # subset: substring for strings, key/value subset for dicts, membership for lists, else exact.
    if isinstance(actual, dict) and isinstance(matcher.value, dict):
        return all(
            key in actual and _arg_matches(actual[key], ArgMatcher(matcher="subset", value=val))
            for key, val in matcher.value.items()
        )
    if isinstance(actual, list) and isinstance(matcher.value, list):
        return all(item in actual for item in matcher.value)
    if isinstance(actual, str) and isinstance(matcher.value, str):
        return matcher.value in actual
    return bool(actual == matcher.value)


def _args_mismatch_reason(
    actual_args: dict[str, Any], expected_args: dict[str, ArgMatcher]
) -> str | None:
    """`None` if every expected argument matches; otherwise a description of the first
    mismatch, for the failure message."""
    for key, matcher in expected_args.items():
        if key not in actual_args:
            return f"argument {key!r} missing (expected {matcher.matcher}={matcher.value!r})"
        if not _arg_matches(actual_args[key], matcher):
            return (
                f"argument {key!r}={actual_args[key]!r} does not satisfy "
                f"{matcher.matcher}={matcher.value!r}"
            )
    return None


def _describe_args(args: dict[str, ArgMatcher]) -> str:
    return "{" + ", ".join(f"{k}: {m.matcher}={m.value!r}" for k, m in args.items()) + "}"


def match_tool_called(
    spans: list[Span], case_id: str, tool_name: str, *, required: bool = True
) -> StepAssertionResult:
    """Pass if `tool_name` was called at least once, regardless of its arguments."""
    calls = _tool_calls(spans, tool_name)
    return make_result(
        assertion_id=f"tool_called:{tool_name}",
        case_id=case_id,
        passed=bool(calls),
        required=required,
        expected=f"a call to {tool_name}",
        actual=f"{len(calls)} call(s)" if calls else "no matching call",
        step_index=calls[0].step_index if calls else None,
    )


def match_not_called(
    spans: list[Span], case_id: str, tool_name: str, *, required: bool = True
) -> StepAssertionResult:
    """Pass if `tool_name` was never called — for a golden case's `forbidden_tools`."""
    calls = _tool_calls(spans, tool_name)
    return make_result(
        assertion_id=f"not_called:{tool_name}",
        case_id=case_id,
        passed=not calls,
        required=required,
        expected=f"no call to {tool_name}",
        actual="none" if not calls else f"called at step(s) {[c.step_index for c in calls]}",
        step_index=calls[0].step_index if calls else None,
    )


def match_max_calls(
    spans: list[Span], case_id: str, tool_name: str, max_count: int, *, required: bool = True
) -> StepAssertionResult:
    calls = _tool_calls(spans, tool_name)
    return make_result(
        assertion_id=f"max_calls:{tool_name}",
        case_id=case_id,
        passed=len(calls) <= max_count,
        required=required,
        expected=f"at most {max_count} call(s) to {tool_name}",
        actual=f"{len(calls)} call(s)",
    )


def match_tool_args(
    spans: list[Span],
    case_id: str,
    tool_name: str,
    args: dict[str, ArgMatcher],
    *,
    required: bool = True,
) -> StepAssertionResult:
    """Pass if *some* call to `tool_name` satisfies every argument matcher in `args`.

    Existential, not "the first call" or "the Nth call": a golden case says "the agent looked up
    this order," not "the agent's third tool call was this specific order lookup." `order`
    (below) is the matcher for call sequencing.
    """
    calls = _tool_calls(spans, tool_name)
    if not calls:
        return make_result(
            assertion_id=f"tool_args:{tool_name}",
            case_id=case_id,
            passed=False,
            required=required,
            expected=f"a call to {tool_name} with {_describe_args(args)}",
            actual="no call to that tool at all",
        )
    first_mismatch: tuple[int, str] | None = None
    for call in calls:
        reason = _args_mismatch_reason(call.arguments, args)
        if reason is None:
            return make_result(
                assertion_id=f"tool_args:{tool_name}",
                case_id=case_id,
                passed=True,
                required=required,
                expected=_describe_args(args),
                actual=str(call.arguments),
                step_index=call.step_index,
            )
        if first_mismatch is None:
            first_mismatch = (call.step_index, reason)
    assert first_mismatch is not None  # calls is non-empty, so the loop set this
    return make_result(
        assertion_id=f"tool_args:{tool_name}",
        case_id=case_id,
        passed=False,
        required=required,
        expected=_describe_args(args),
        actual=f"no call satisfied it — e.g. step {first_mismatch[0]}: {first_mismatch[1]}",
        step_index=calls[0].step_index,
    )


def _is_subsequence(expected: list[str], actual: list[str]) -> bool:
    position = 0
    for item in expected:
        while position < len(actual) and actual[position] != item:
            position += 1
        if position >= len(actual):
            return False
        position += 1
    return True


def _is_contiguous_subsequence(expected: list[str], actual: list[str]) -> bool:
    if not expected:
        return True
    span = len(expected)
    return any(
        actual[start : start + span] == expected for start in range(len(actual) - span + 1)
    )


def match_order(
    spans: list[Span],
    case_id: str,
    expected_tool_names: list[str],
    mode: Literal["subsequence", "strict"] = "subsequence",
    *,
    required: bool = True,
) -> StepAssertionResult:
    """`subsequence`: the expected calls appear in that relative order, other calls allowed
    between them. `strict`: they appear as an exact contiguous block."""
    actual_sequence = [s.tool_name for s in spans if isinstance(s, ToolCallSpan)]
    passed = (
        _is_contiguous_subsequence(expected_tool_names, actual_sequence)
        if mode == "strict"
        else _is_subsequence(expected_tool_names, actual_sequence)
    )
    return make_result(
        assertion_id=f"order:{mode}",
        case_id=case_id,
        passed=passed,
        required=required,
        expected=f"{mode} {expected_tool_names}",
        actual=str(actual_sequence),
    )


def _derive_action(spans: list[Span]) -> str:
    action = "no_action"
    for span in spans:
        if (
            isinstance(span, ToolCallSpan)
            and not span.is_error
            and span.tool_name in _ACTION_TOOLS
        ):
            action = _ACTION_TOOLS[span.tool_name]
    return action


def _derive_labels(spans: list[Span]) -> list[str]:
    return [
        str(span.arguments["label"])
        for span in spans
        if isinstance(span, ToolCallSpan)
        and span.tool_name == "add_label"
        and not span.is_error
        and "label" in span.arguments
    ]


def match_final_outcome(
    spans: list[Span], case_id: str, expected: ExpectedOutcome, *, required: bool = True
) -> StepAssertionResult:
    """`action` is derived from the trace (last successful action-tool call); `label` is
    checked against every successful `add_label` call's `label` argument, not just the last."""
    actual_action = _derive_action(spans)
    actual_labels = _derive_labels(spans)
    action_ok = expected.action is None or expected.action == actual_action
    label_ok = expected.label is None or expected.label in actual_labels
    return make_result(
        assertion_id="final_outcome",
        case_id=case_id,
        passed=action_ok and label_ok,
        required=required,
        expected=f"action={expected.action!r} label={expected.label!r}",
        actual=f"action={actual_action!r} labels={actual_labels!r}",
    )


def match_step_budget(
    spans: list[Span], case_id: str, max_steps: int, *, required: bool = True
) -> StepAssertionResult:
    root = next((s for s in spans if isinstance(s, AgentRunSpan)), None)
    if root is None:
        return make_result(
            assertion_id="step_budget",
            case_id=case_id,
            passed=False,
            required=required,
            expected=f"<= {max_steps} step(s)",
            actual="no agent_run span found (incomplete trace)",
        )
    return make_result(
        assertion_id="step_budget",
        case_id=case_id,
        passed=root.step_count <= max_steps,
        required=required,
        expected=f"<= {max_steps} step(s)",
        actual=f"{root.step_count} step(s)",
    )


def match_cost_budget(
    spans: list[Span], case_id: str, max_micro_dollars: int, *, required: bool = True
) -> StepAssertionResult:
    """Reuses `tripwire.cost.rollup.rollup` for the total rather than re-summing spans itself —
    one function computes cost; this matcher only compares it to the budget."""
    if not spans:
        return make_result(
            assertion_id="cost_budget",
            case_id=case_id,
            passed=False,
            required=required,
            expected=f"<= {max_micro_dollars} micro-dollars",
            actual="no spans recorded",
        )
    run_id = spans[0].run_id
    try:
        rolled = rollup(run_id, list(spans))
    except (IncompleteRunError, SpanRunMismatchError, DuplicateSpanError) as exc:
        return make_result(
            assertion_id="cost_budget",
            case_id=case_id,
            passed=False,
            required=required,
            expected=f"<= {max_micro_dollars} micro-dollars",
            actual=f"cost could not be computed: {exc}",
        )
    return make_result(
        assertion_id="cost_budget",
        case_id=case_id,
        passed=rolled.total_micro_dollars <= max_micro_dollars,
        required=required,
        expected=f"<= {max_micro_dollars} micro-dollars",
        actual=f"{rolled.total_micro_dollars} micro-dollars",
    )
