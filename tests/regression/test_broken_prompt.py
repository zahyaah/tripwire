"""Broken-prompt demo (tasks/todo.md Task 19): prove the regression gate catches a routing
regression — a prompt change that makes the agent escalate when it should not fails the build.

This does not need a real API call or a committed cassette: `run_case` in `mode="record"` against
a scripted, in-memory fake client (the exact pattern `tests/unit/test_loop.py` already uses for
the record/replay round trip) exercises the real pipeline end to end — `run_agent`, the
assertions, `build_summary`, `evaluate_gate` — with a scripted response standing in for what a
genuinely broken prompt would cause a live model to do. That live confirmation is still pending
real cassette recording (`docs/known-gaps.md`, `docs/demo.md`); what this file proves right now,
for real, is that the harness's own routing-regression detection works.

No `pytest.mark.regression` here on purpose: unlike `test_golden_set.py`, nothing in this file
reads a committed cassette, so it runs — and passes — under a plain `uv run pytest -q`, same as
any other unit test (tasks/todo.md Task 19 verification: "green... it asserts failure of the
gate, not of the suite").
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletion

from agents.inbox_triage.loop import UnknownPromptVariantError, load_system_prompt
from tripwire.assertions import run_case
from tripwire.core.golden import ExpectedOutcome, GoldenCase, GoldenCaseInput
from tripwire.core.store import TraceReader, default_trace_path
from tripwire.cost.prices import PriceEntry
from tripwire.cost.rollup import RunRollup, rollup
from tripwire.data import generate_corpus
from tripwire.report.gate import evaluate_gate
from tripwire.report.summary import build_summary

_PRICE_TABLE = {
    "test-model": PriceEntry(
        model="test-model",
        as_of=date(2026, 1, 1),
        prompt_micro_dollars_per_million=1_000,
        completion_micro_dollars_per_million=2_000,
    )
}

_STARTED = datetime(2026, 9, 23, tzinfo=UTC)


def _completion(
    *,
    finish_reason: str,
    tool_calls: list[dict[str, Any]] | None = None,
    content: str | None = None,
) -> ChatCompletion:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "choices": [
                {"index": 0, "finish_reason": finish_reason, "logprobs": None, "message": message}
            ],
            "created": 1234,
            "model": "test-model",
            "object": "chat.completion",
            "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        }
    )


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class _ScriptedCompletionsResource:
    """Returns completions from a fixed script, in call order -- ignores request content
    entirely, including which system prompt was sent (mirrors test_loop.py's own helper)."""

    def __init__(self, script: list[ChatCompletion]) -> None:
        self._script = script
        self.call_count = 0

    def create(self, **kwargs: Any) -> ChatCompletion:
        completion = self._script[min(self.call_count, len(self._script) - 1)]
        self.call_count += 1
        return completion


class _ScriptedClient:
    def __init__(self, script: list[ChatCompletion]) -> None:
        self.chat = type("Chat", (), {"completions": _ScriptedCompletionsResource(script)})()


def _case(thread_id: str) -> GoldenCase:
    return GoldenCase(
        case_id="broken-prompt-demo-escalation",
        intent="angry_escalation",
        input=GoldenCaseInput(thread_id=thread_id),
        forbidden_tools=["escalate"],
        expected_outcome=ExpectedOutcome(action="replied"),
    )


def _correct_response_script(thread_id: str) -> list[ChatCompletion]:
    """What a prompt that reads to the end before reacting produces: reply, don't escalate."""
    return [
        _completion(
            finish_reason="tool_calls",
            tool_calls=[_tool_call("call_0", "get_thread", {"thread_id": thread_id})],
        ),
        _completion(
            finish_reason="tool_calls",
            tool_calls=[
                _tool_call(
                    "call_1",
                    "draft_reply",
                    {"thread_id": thread_id, "body": "Happy to help with that right away."},
                )
            ],
        ),
        _completion(finish_reason="stop", content="Replied to the actual, small ask."),
    ]


def _broken_response_script(thread_id: str) -> list[ChatCompletion]:
    """What `always_escalate.md` causes: escalate on the opening line's tone, nothing else."""
    return [
        _completion(
            finish_reason="tool_calls",
            tool_calls=[_tool_call("call_0", "get_thread", {"thread_id": thread_id})],
        ),
        _completion(
            finish_reason="tool_calls",
            tool_calls=[
                _tool_call(
                    "call_1", "escalate", {"thread_id": thread_id, "reason": "angry customer"}
                )
            ],
        ),
        _completion(finish_reason="stop", content="Escalated immediately on tone."),
    ]


def _run_case_with_script(
    *, thread_id: str, script: list[ChatCompletion], prompt_variant: str | None, tmp_path: Path
) -> tuple[Any, RunRollup]:
    corpus = generate_corpus(seed=1, thread_count=10)
    case = _case(thread_id)
    result = run_case(
        case,
        corpus=corpus,
        mode="record",
        model="test-model",
        cassettes_dir=tmp_path / "cassettes",
        runs_dir=tmp_path / "runs",
        client=_ScriptedClient(script),  # type: ignore[arg-type]
        price_table=_PRICE_TABLE,
        prompt_variant=prompt_variant,
    )
    _run_record, spans = TraceReader.load(default_trace_path(result.run_id, tmp_path / "runs"))
    rolled = rollup(result.run_id, spans)
    return result, rolled


# --- prompt_variant wiring is real, independent of the scripted-client demonstration below -----


def test_load_system_prompt_default_and_variant_differ() -> None:
    default_prompt = load_system_prompt()
    broken_prompt = load_system_prompt("always_escalate")
    assert default_prompt != broken_prompt
    assert "escalate to a human immediately" in broken_prompt
    assert "escalate to a human immediately" not in default_prompt


def test_unknown_prompt_variant_raises_a_named_error() -> None:
    try:
        load_system_prompt("does-not-exist")
    except UnknownPromptVariantError as exc:
        assert "does-not-exist" in str(exc)
    else:
        raise AssertionError("expected UnknownPromptVariantError")


def test_prompt_variant_changes_the_recorded_prompt_hash(tmp_path: Path) -> None:
    thread_id = generate_corpus(seed=1, thread_count=10).threads[0].thread_id
    default_result, _ = _run_case_with_script(
        thread_id=thread_id,
        script=_correct_response_script(thread_id),
        prompt_variant=None,
        tmp_path=tmp_path / "default",
    )
    broken_result, _ = _run_case_with_script(
        thread_id=thread_id,
        script=_correct_response_script(thread_id),  # same script -- only the prompt differs
        prompt_variant="always_escalate",
        tmp_path=tmp_path / "broken",
    )
    default_run, _ = TraceReader.load(
        default_trace_path(default_result.run_id, tmp_path / "default" / "runs")
    )
    broken_run, _ = TraceReader.load(
        default_trace_path(broken_result.run_id, tmp_path / "broken" / "runs")
    )
    assert default_run.prompt_hash != broken_run.prompt_hash


# --- the actual regression demonstration ---------------------------------------------------


def test_gate_fails_on_the_broken_prompt_variant_and_names_the_case(tmp_path: Path) -> None:
    thread_id = generate_corpus(seed=1, thread_count=10).threads[0].thread_id

    good_result, good_rollup = _run_case_with_script(
        thread_id=thread_id,
        script=_correct_response_script(thread_id),
        prompt_variant=None,
        tmp_path=tmp_path / "good",
    )
    broken_result, broken_rollup = _run_case_with_script(
        thread_id=thread_id,
        script=_broken_response_script(thread_id),
        prompt_variant="always_escalate",
        tmp_path=tmp_path / "broken",
    )

    # The suite's own required-assertion verdict: the default prompt's run passes, the broken
    # variant's run doesn't (it called the forbidden `escalate`).
    assert good_result.passed is True
    assert broken_result.passed is False
    assert broken_result.blocks_gate is True

    case_intents = {"broken-prompt-demo-escalation": "angry_escalation"}
    baseline_summary = build_summary(
        suite_run_id="suite_demo_baseline",
        model="test-model",
        llm_mode="record",
        prompt_hash="baseline",
        started_at=_STARTED,
        case_intents=case_intents,
        case_results=[good_result],
        rollups={good_result.run_id: good_rollup},
    )
    broken_summary = build_summary(
        suite_run_id="suite_demo_broken",
        model="test-model",
        llm_mode="record",
        prompt_hash="broken",
        started_at=_STARTED,
        case_intents=case_intents,
        case_results=[broken_result],
        rollups={broken_result.run_id: broken_rollup},
    )

    gate_result = evaluate_gate(broken_summary, baseline_summary)

    assert gate_result.passed is False
    violation = next(v for v in gate_result.violations if v.check == "required_assertions")
    assert "broken-prompt-demo-escalation" in violation.delta
    assert any(
        "escalate" in a.detail for a in broken_result.assertion_results if not a.passed
    ), "expected a failing assertion naming the forbidden `escalate` call"
