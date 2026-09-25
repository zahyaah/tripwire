"""The manual agentic loop: call the model through the gateway, dispatch tool calls, feed
results back, emit spans, and stop on completion, a budget, or an error.

Manual, not the SDK's tool-runner helper: the harness has to own every step boundary itself to
record spans and enforce budgets (SPEC.md § Commands / Model-call rules).

Tool-result convention note: tasks/todo.md's Task 7 acceptance criteria were written before the
NVIDIA provider swap and describe Anthropic's convention ("all results returned in a single user
message" — Anthropic bundles tool_result content blocks into one user message). This gateway
targets an OpenAI-compatible API (source-verified, see gateway.py), whose actual convention is
one `role: "tool"` message per tool call, each carrying its own `tool_call_id`. That is what this
loop does — bundling them into one message would not be a stylistic choice, it would be wrong
against the real API. All of a step's tool results are still appended together, immediately
after that step's assistant message and before the next model call, which is the part of the
original intent (parallel calls resolved as a unit) that actually carries over.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.inbox_triage.schemas import TOOL_SCHEMAS
from agents.inbox_triage.state import InboxState
from agents.inbox_triage.tools import run_tool
from tripwire.core import (
    AgentRunSpan,
    ToolCallSpan,
    TraceWriter,
    finish_run,
    new_span_id,
    start_run,
)
from tripwire.cost.prices import price_call
from tripwire.llm.gateway import ModelGateway, ModelRequest, usage_from_completion

_PROMPT_PATH = Path(__file__).with_name("prompt.md")
_PROMPT_VARIANTS_DIR = Path(__file__).with_name("prompt_variants")
_RESULT_TRUNCATE_BYTES = 16_384
HARNESS_VERSION = "0.1.0"


class UnknownPromptVariantError(Exception):
    """`--prompt-variant <name>` was given but `prompt_variants/<name>.md` doesn't exist."""


def load_system_prompt(variant: str | None = None) -> str:
    """The system prompt, read fresh from disk every call — never cached at import time, so an
    edit is picked up (and its hash changes) without restarting anything.

    `variant`, when given, loads `prompt_variants/<variant>.md` instead of the default
    `prompt.md` — a deliberately different prompt committed on purpose (tasks/todo.md Task 19:
    "prove a prompt change that breaks routing fails the build"), never the production prompt
    edited in place.
    """
    if variant is None:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    variant_path = _PROMPT_VARIANTS_DIR / f"{variant}.md"
    if not variant_path.exists():
        raise UnknownPromptVariantError(
            f"no prompt variant {variant!r} at {variant_path} "
            f"(available: {sorted(p.stem for p in _PROMPT_VARIANTS_DIR.glob('*.md'))})"
        )
    return variant_path.read_text(encoding="utf-8")


class LoopConfig(BaseModel):
    """Budgets and model config for one run. See SPEC-synthetic-data.md's golden case `budgets`
    block — a golden case can override these per case once Task 8 wires that through."""

    model_config = ConfigDict(frozen=True)

    model: str
    max_steps: int = Field(default=8, ge=1)
    max_micro_dollars: int = Field(default=40_000, ge=0)
    max_tokens_per_call: int = Field(default=2_000, ge=1)


class LoopResult(BaseModel):
    """What `run_agent` returns. The trace file (written as a side effect) is the real record —
    this is a convenience summary for callers that don't want to re-read it immediately."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    outcome: Literal["completed", "budget_exceeded", "error"]
    final_message: str | None = None
    step_count: int
    error: str | None = None


def run_agent(
    *,
    thread_id: str,
    state: InboxState,
    gateway: ModelGateway,
    run_id: str,
    writer: TraceWriter,
    config: LoopConfig,
    suite_run_id: str | None = None,
    case_id: str | None = None,
    prompt_variant: str | None = None,
) -> LoopResult:
    """Triage one thread end to end. Emits exactly one `agent_run` span, one `model_call` span
    per model call (via the gateway), and one `tool_call` span per tool call."""
    system_prompt = load_system_prompt(prompt_variant)
    run = start_run(
        run_id=run_id,
        agent_name="inbox_triage",
        model=config.model,
        system_prompt=system_prompt,
        harness_version=HARNESS_VERSION,
        llm_mode=gateway.mode,
        suite_run_id=suite_run_id,
        case_id=case_id,
    )
    writer.write_run(run)

    root_span_id = new_span_id()
    run_started_at = time.monotonic()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"A new thread has arrived: thread_id={thread_id}. "
                "Use the available tools to triage it."
            ),
        },
    ]

    outcome: Literal["completed", "budget_exceeded", "error"] = "completed"
    error: str | None = None
    final_message: str | None = None
    step_index = 0
    total_micro_dollars = 0

    while True:
        if step_index >= config.max_steps:
            outcome = "budget_exceeded"
            break

        request = ModelRequest(
            model=config.model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            max_tokens=config.max_tokens_per_call,
        )
        try:
            completion = gateway.create(
                request, step_index=step_index, parent_span_id=root_span_id
            )
        except Exception as exc:
            # Broad on purpose: the loop's job here is to record and stop, not to classify every
            # possible SDK/network failure — the gateway already emitted its own is_error span
            # for this call before raising (see gateway.py), so nothing about the failure itself
            # is lost by catching broadly at this boundary.
            outcome = "error"
            error = f"{type(exc).__name__}: {exc}"
            break

        choice = completion.choices[0]
        message = choice.message
        total_micro_dollars += price_call(
            config.model, usage_from_completion(completion), table=gateway.price_table
        )

        # Echo the SDK message back as raw a dict as the API will accept: Gemini 3 needs
        # `extra_content.google.thought_signature` retained on re-sent tool-call parts (live
        # 400: "Function call is missing a thought_signature", 2026-09-23) but rejects any
        # explicit null field (live 400: "Value is not a struct: null"), so model_dump then
        # drop nothing keys at the message level. This is why it's not the hand-built
        # {"role","content","tool_calls"} subset the loop used to echo.
        assistant_entry: dict[str, Any] = {
            k: v for k, v in message.model_dump(mode="json").items() if v is not None
        }
        messages.append(assistant_entry)

        if choice.finish_reason == "tool_calls" and message.tool_calls:
            for tc in message.tool_calls:
                # Every tool_call_id from this turn must get a role="tool" response before the
                # next model call, or the API rejects the next request — including a tool call
                # this loop can't actually run. TOOL_SCHEMAS declares only function-type tools,
                # so a "custom" (freeform) tool call is unexpected, not routine, but it still
                # gets an error result rather than being silently dropped or crashing the loop.
                if tc.type != "function":
                    result = {"ok": False, "error": f"unsupported tool call type {tc.type!r}"}
                else:
                    result = _dispatch_tool_call(
                        state=state,
                        writer=writer,
                        run_id=run_id,
                        parent_span_id=root_span_id,
                        step_index=step_index,
                        tool_name=tc.function.name,
                        raw_arguments_json=tc.function.arguments,
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, separators=(",", ":")),
                    }
                )
        elif choice.finish_reason == "stop":
            final_message = message.content
            outcome = "completed"
            step_index += 1
            break
        else:
            # "length" (truncated at max_tokens), a content filter, or any other finish_reason
            # this loop doesn't have a defined action for. Treated as an error outcome rather
            # than silently accepted — an unhandled finish_reason with no tool calls and no
            # "stop" is not a completed triage.
            outcome = "error"
            error = f"unhandled finish_reason={choice.finish_reason!r}"
            step_index += 1
            break

        if total_micro_dollars > config.max_micro_dollars:
            outcome = "budget_exceeded"
            step_index += 1
            break

        step_index += 1

    run_latency_ms = int((time.monotonic() - run_started_at) * 1000)
    writer.append(
        AgentRunSpan(
            span_id=root_span_id,
            parent_span_id=None,
            run_id=run_id,
            step_index=0,
            started_at=datetime.now(UTC),
            latency_ms=run_latency_ms,
            step_count=step_index,
            total_micro_dollars=total_micro_dollars,
        )
    )
    writer.write_run(finish_run(run, outcome=outcome, error=error))

    return LoopResult(
        run_id=run_id,
        outcome=outcome,
        final_message=final_message,
        step_count=step_index,
        error=error,
    )


def _dispatch_tool_call(
    *,
    state: InboxState,
    writer: TraceWriter,
    run_id: str,
    parent_span_id: str,
    step_index: int,
    tool_name: str,
    raw_arguments_json: str,
) -> dict[str, Any]:
    span_id = new_span_id()
    started_at = datetime.now(UTC)
    call_start = time.monotonic()

    try:
        raw_arguments: dict[str, Any] = json.loads(raw_arguments_json)
        if not isinstance(raw_arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
    except (json.JSONDecodeError, ValueError):
        result: dict[str, Any] = {
            "ok": False,
            "error": "malformed tool call arguments (not a JSON object)",
        }
        arguments_for_span: dict[str, object] = {"_raw": raw_arguments_json[:200]}
    else:
        result = run_tool(state, tool_name, raw_arguments)
        arguments_for_span = raw_arguments

    latency_ms = int((time.monotonic() - call_start) * 1000)
    summary, total_bytes = _truncate_result(result)
    writer.append(
        ToolCallSpan(
            span_id=span_id,
            parent_span_id=parent_span_id,
            run_id=run_id,
            step_index=step_index,
            started_at=started_at,
            latency_ms=latency_ms,
            is_error=not result.get("ok", False),
            tool_name=tool_name,
            arguments=arguments_for_span,
            result_summary=summary,
            result_bytes=total_bytes,
        )
    )
    return result


def _truncate_result(result: dict[str, Any]) -> tuple[str, int]:
    """Compact JSON of a tool result, truncated for the span if it's large.

    `result_bytes` is always the *untruncated* length — comparing it to `len(result_summary)`
    is how a trace reader detects truncation happened (SPEC-trace-core.md Open Question 2).
    """
    full = json.dumps(result, separators=(",", ":"))
    encoded = full.encode("utf-8")
    if len(encoded) <= _RESULT_TRUNCATE_BYTES:
        return full, len(encoded)
    return encoded[:_RESULT_TRUNCATE_BYTES].decode("utf-8", errors="ignore"), len(encoded)
