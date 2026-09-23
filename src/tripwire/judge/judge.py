"""The LLM-as-judge call: score a recorded run's transcript against the rubric.

Structured output via `response_format` (JSON-schema mode) through the same `ModelGateway` every
other call goes through — cassetted, priced, and traced identically to an agent's own model
calls, just tagged `kind="judge_call"` (SPEC-trace-core.md § Records). No prefill, no prose
parsing: the judge's answer is validated JSON or an explicit error, never a best-effort guess.
"""

from __future__ import annotations

from pathlib import Path

from openai.types.chat import ChatCompletion
from pydantic import ValidationError

from tripwire.core.records import Span, ToolCallSpan
from tripwire.data.models import Corpus
from tripwire.judge.rubric import RUBRIC_VERSION, RubricScore, rubric_response_format
from tripwire.llm.gateway import ModelGateway, ModelRequest

_PROMPT_PATH = Path(__file__).with_name("prompt.md")


class JudgeOutputError(Exception):
    """The judge's response didn't parse into a `RubricScore`.

    Raised rather than returning a partial or default-filled score — a malformed judge response
    is a signal something is wrong (a schema mismatch, a provider quirk), not a 0/1 to silently
    fold into the calibration statistics (tasks/todo.md Task 12 acceptance criterion).
    """


def load_judge_prompt() -> str:
    """The judge's system prompt, read fresh from `prompt.md` every call (mirrors
    `agents.inbox_triage.loop.load_system_prompt` — never cached at import time)."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


def render_thread(corpus: Corpus, thread_id: str) -> str:
    """The original customer thread, for corpus context. Deliberately reads only from `Corpus` —
    never from a `GoldenCase` (which this module never imports at all): the judge must not see
    the golden expectations or a human label (tasks/todo.md Task 12 acceptance criterion)."""
    thread = next((t for t in corpus.threads if t.thread_id == thread_id), None)
    if thread is None:
        return f"[thread {thread_id!r} not found in corpus]"
    parts = [f"Thread {thread_id}:"]
    for message in thread.messages:
        parts.append(f"From: {message.sender_email}\nSubject: {message.subject}\n{message.body}")
    return "\n\n".join(parts)


def render_transcript(spans: list[Span]) -> str:
    """The agent's tool-call transcript, in step order. The agent's final reply text (if any)
    lives inside a `draft_reply` tool call's `body` argument — tool calls are the complete
    record of what the agent did; there is no separate "final message" span to also include."""
    lines: list[str] = []
    for span in spans:
        if not isinstance(span, ToolCallSpan):
            continue
        status = "ERROR" if span.is_error else "ok"
        lines.append(
            f"[step {span.step_index}] {span.tool_name}({span.arguments}) -> "
            f"{status}: {span.result_summary}"
        )
    return "\n".join(lines) if lines else "(the agent made no tool calls)"


def judge_run(
    *,
    gateway: ModelGateway,
    model: str,
    thread_id: str,
    spans: list[Span],
    corpus: Corpus,
    step_index: int,
    parent_span_id: str | None,
) -> RubricScore:
    """Score one run's transcript against the rubric. Emits exactly one `judge_call` span via
    the gateway (same call path as any other model call)."""
    system_prompt = load_judge_prompt()
    user_content = (
        f"{render_thread(corpus, thread_id)}\n\n"
        f"--- Agent transcript ---\n{render_transcript(spans)}"
    )
    request = ModelRequest(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format=rubric_response_format(),
    )
    completion: ChatCompletion = gateway.create(
        request,
        step_index=step_index,
        parent_span_id=parent_span_id,
        kind="judge_call",
        rubric_version=RUBRIC_VERSION,
    )
    content = completion.choices[0].message.content if completion.choices else None
    if content is None:
        raise JudgeOutputError("judge response had no content to parse")
    try:
        return RubricScore.model_validate_json(content)
    except ValidationError as exc:
        raise JudgeOutputError(f"judge response did not match the rubric schema: {exc}") from exc
