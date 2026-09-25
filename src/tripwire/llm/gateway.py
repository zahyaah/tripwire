"""The instrumented model gateway: every call emits a span, and calls can be recorded to /
replayed from a cassette (SPEC-trace-core.md § Model gateway, § Cassettes).

Provider swap #2 (2026-09-23): NVIDIA -> Gemini, per user direction. Facts below are
live-verified against the real Gemini OpenAI-compat endpoint
(https://generativelanguage.googleapis.com/v1beta/openai/, model `gemini-3-flash-preview`) with
actual API calls, not just a docs page — the same day's `gemini-3.8-flash` id (from
client.models.list()) 503'd on every call and the docs-listed `gemini-3-flash` 404'd; the
preview id is the one that actually returns completions, which is exactly why this project
doesn't trust a doc over a real call:

- Client construction (`OpenAI(api_key=..., base_url=...)`) and the exception hierarchy
  (`APITimeoutError < APIConnectionError`; `RateLimitError`/`NotFoundError`/`AuthenticationError`/
  `BadRequestError` < `APIStatusError`; both < `APIError`) — openai-python README.md and the
  installed package's own MRO; provider-agnostic, unaffected by the swap.
- `max_tokens` works (tested live); `max_completion_tokens` untested on this endpoint.
- Native `tool_calls` confirmed live: a real call with a `strict: true` function tool returned
  `finish_reason="tool_calls"` and a correctly-shaped `tool_calls[0].function`.
- `usage.{prompt_tokens,completion_tokens}` are present, but `usage.total_tokens` on Gemini 3
  exceeds their sum by 40-90% with no `*_tokens_details` populated to explain it — hidden
  thinking tokens billed but not itemized. See `TokenUsage.reasoning_tokens` (Task 2) and
  `usage_from_completion` below, which exists specifically to recover this gap.
- Sync streaming via `client.chat.completions.stream(...)` + `.get_final_completion()` — the
  installed SDK's `helpers.md` (documents the async form; the sync method exists with the same
  contract, confirmed via `inspect.signature`); not yet re-verified live against Gemini
  specifically (SPEC.md tracks this as an open item).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, cast

import openai
from openai import OpenAI
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ConfigDict

from tripwire.core.ids import new_span_id
from tripwire.core.records import JudgeCallSpan, ModelCallSpan, TokenUsage
from tripwire.core.store import TraceWriter
from tripwire.cost.prices import PRICE_TABLE, PriceEntry, UnknownModelError, price_call
from tripwire.llm.cassettes import CassetteRecord, CassetteStore, compute_cassette_key

# Above this many requested output tokens, stream rather than block: a large max_tokens on a slow
# generation risks the client's HTTP timeout before a non-streaming call returns. The threshold
# itself is this project's own choice (not sourced) — openai-python's docs recommend streaming
# for "long-running" completions without giving a token figure.
STREAMING_MAX_TOKENS_THRESHOLD = 4096


class ModelRequest(BaseModel):
    """What the gateway is asked to do.

    Mirrors the openai SDK's own `chat.completions.create` parameters directly (same field names)
    rather than inventing a parallel shape, so building a request from an agent's tool/message
    state is a straight passthrough with no translation layer to keep in sync.

    `frozen=True` is shallow: it blocks reassigning `request.messages = ...` but not mutating the
    list/dicts already inside it (`request.messages.append(...)`). Callers must treat the message
    history as owned by the request once constructed, not shared and mutated in place — a shared,
    mutated list would silently change both the API call and the cassette key underneath it.

    `max_tokens` should always be set explicitly by callers: the streaming threshold
    (`STREAMING_MAX_TOKENS_THRESHOLD`) only triggers when `max_tokens` is given, so an unset
    `max_tokens` gets no streaming protection against a long-running generation's HTTP timeout —
    exactly the case with the least caller-side control over how long the response might run.
    """

    model_config = ConfigDict(frozen=True)

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    max_tokens: int | None = None


class CassetteMissError(Exception):
    """`replay` mode was asked for a request with no recorded cassette.

    Carries `cassette_key` on the instance so a caller with more context (a golden case id, an
    agent step) can catch this, add that context, and re-raise — the gateway itself has no idea
    what case or step it's serving.
    """

    def __init__(self, cassette_key: str) -> None:
        self.cassette_key = cassette_key
        super().__init__(
            f"no cassette for key {cassette_key!r} in replay mode — "
            "re-record with --mode record (or use --mode live for a one-off call); "
            "see SPEC-trace-core.md § Cassettes"
        )


class ModelGateway:
    """Wraps an `openai` client (pointed at an OpenAI-compatible endpoint) so every call emits
    a span and can be recorded to / replayed from a cassette.

    One gateway per run: `run_id` is fixed at construction and stamped on every span it emits,
    matching the correlation-id convention from Task 2.
    """

    def __init__(
        self,
        *,
        run_id: str,
        writer: TraceWriter,
        mode: Literal["live", "record", "replay"],
        client: OpenAI | None = None,
        cassettes: CassetteStore | None = None,
        price_table: Mapping[str, PriceEntry] | None = None,
    ) -> None:
        if mode in ("live", "record") and client is None:
            raise ValueError(
                f"mode={mode!r} calls the API and needs a client, but none was given"
            )
        self._run_id = run_id
        self._writer = writer
        self._mode = mode
        self._client = client
        self._cassettes = cassettes or CassetteStore()
        self._price_table = price_table if price_table is not None else PRICE_TABLE

    @property
    def mode(self) -> Literal["live", "record", "replay"]:
        """The mode this gateway was constructed with.

        Exposed so a caller building a `RunRecord` (`agents/inbox_triage/loop.py`) records the
        mode that actually ran, rather than hardcoding a guess that can silently disagree with
        what the gateway did.
        """
        return self._mode

    @property
    def price_table(self) -> Mapping[str, PriceEntry]:
        """The price table this gateway prices calls against.

        Exposed so a caller that needs to price something itself (the agent loop's running cost
        budget, say — see agents/inbox_triage/loop.py) uses the *same* table the gateway used for
        the spans it already wrote, rather than risking a second, differently-configured table
        that would silently disagree with the trace.
        """
        return self._price_table

    def create(
        self,
        request: ModelRequest,
        *,
        step_index: int,
        parent_span_id: str | None,
        kind: Literal["model_call", "judge_call"] = "model_call",
        rubric_version: str | None = None,
    ) -> ChatCompletion:
        """Make one model call, emitting exactly one span (`model_call` or `judge_call`).

        Raises `CassetteMissError` in replay mode with no matching cassette — never falls back to
        the network. Raises the openai SDK's own exception types on a live/record failure; the
        failure is still recorded as an `is_error=True` span (zeroed usage/cost, since none was
        returned — see `_write_span`'s docstring) before the exception propagates.
        """
        if kind == "judge_call" and rubric_version is None:
            raise ValueError("kind='judge_call' requires rubric_version")

        key = compute_cassette_key(
            model=request.model,
            messages=request.messages,
            tools=request.tools,
            tool_choice=request.tool_choice,
            response_format=request.response_format,
            max_tokens=request.max_tokens,
        )
        span_id = new_span_id()

        if self._mode == "replay":
            completion, latency_ms = self._replay(key)
        else:
            completion, latency_ms = self._call_live_or_record(
                request, key, span_id, step_index, parent_span_id, kind, rubric_version
            )

        usage = usage_from_completion(completion)
        finish_reason = completion.choices[0].finish_reason if completion.choices else None

        # price_call can raise UnknownModelError — a real, expected failure (a model missing from
        # PRICE_TABLE), not exotic. The API call already happened and usage is already known at
        # this point, so a pricing failure must not cost us the span: write it with the real
        # usage and is_error=True (cost genuinely couldn't be determined, which is different from
        # "cost was zero"), then re-raise so the caller still learns pricing failed. Without this,
        # a record-mode call would leave an orphaned cassette on disk with no matching span, and a
        # replay-mode call would silently vanish from the trace despite having "succeeded".
        try:
            micro_dollars = price_call(request.model, usage, table=self._price_table)
        except UnknownModelError:
            self._write_span(
                span_id=span_id,
                parent_span_id=parent_span_id,
                step_index=step_index,
                latency_ms=latency_ms,
                model=request.model,
                stop_reason=finish_reason,
                usage=usage,
                micro_dollars=0,
                cassette_key=key,
                cassette_hit=self._mode == "replay",
                is_error=True,
                kind=kind,
                rubric_version=rubric_version,
            )
            raise

        self._write_span(
            span_id=span_id,
            parent_span_id=parent_span_id,
            step_index=step_index,
            latency_ms=latency_ms,
            model=request.model,
            stop_reason=finish_reason,
            usage=usage,
            micro_dollars=micro_dollars,
            cassette_key=key,
            cassette_hit=self._mode == "replay",
            is_error=False,
            kind=kind,
            rubric_version=rubric_version,
        )
        return completion

    def _replay(self, key: str) -> tuple[ChatCompletion, int]:
        cassette = self._cassettes.load(key)
        if cassette is None:
            raise CassetteMissError(key)
        return ChatCompletion.model_validate(cassette.completion), cassette.latency_ms

    def _call_live_or_record(
        self,
        request: ModelRequest,
        key: str,
        span_id: str,
        step_index: int,
        parent_span_id: str | None,
        kind: Literal["model_call", "judge_call"],
        rubric_version: str | None,
    ) -> tuple[ChatCompletion, int]:
        assert self._client is not None  # enforced in __init__ for these two modes
        start = time.monotonic()
        try:
            completion = self._call_api(request)
        # Caught at the SDK's shared base, not a most-specific-first chain of its subclasses
        # (NotFoundError/RateLimitError/APITimeoutError/APIConnectionError/APIStatusError):
        # every subclass gets identical treatment here — record an is_error=True span, then
        # re-raise the original exception unchanged. "Most specific first" earns its keep when
        # different exception types need different handling; there is no such branch to protect
        # here, and unrolling one into six identical blocks would be complexity with no payoff.
        # The caller (agent loop, Task 7) is where a retry-vs-fail distinction would live, and it
        # still gets the original typed exception to make that call on.
        #
        # openai.LengthFinishReasonError / openai.ContentFilterFinishReasonError are NOT included
        # here by inheritance — verified against the installed SDK, they are siblings of
        # APIError (both extend OpenAIError directly), not subclasses of it. The streaming helper
        # raises LengthFinishReasonError when a strict-schema tool call gets truncated at
        # max_tokens, which this gateway's tool support makes a real, reachable case, not a
        # theoretical one — so both are named explicitly, or the API call happens (and is
        # billable) with no span ever written for it.
        except (
            openai.APIError,
            openai.LengthFinishReasonError,
            openai.ContentFilterFinishReasonError,
        ):
            latency_ms = _elapsed_ms(start)
            self._write_span(
                span_id=span_id,
                parent_span_id=parent_span_id,
                step_index=step_index,
                latency_ms=latency_ms,
                model=request.model,
                stop_reason=None,
                usage=TokenUsage(prompt_tokens=0, completion_tokens=0),
                micro_dollars=0,
                cassette_key=key,
                cassette_hit=False,
                is_error=True,
                kind=kind,
                rubric_version=rubric_version,
            )
            raise
        latency_ms = _elapsed_ms(start)
        if self._mode == "record":
            self._cassettes.save(
                key,
                CassetteRecord(
                    completion=completion.model_dump(mode="json"), latency_ms=latency_ms
                ),
            )
        return completion, latency_ms

    def _call_api(self, request: ModelRequest) -> ChatCompletion:
        assert self._client is not None
        kwargs: dict[str, Any] = {"model": request.model, "messages": request.messages}
        if request.tools is not None:
            kwargs["tools"] = request.tools
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice
        if request.response_format is not None:
            kwargs["response_format"] = request.response_format
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        if request.max_tokens is not None and request.max_tokens > STREAMING_MAX_TOKENS_THRESHOLD:
            # stream_options={"include_usage": True} is required, not optional: verified against
            # the installed SDK's own source (openai/lib/streaming/chat/_completions.py) that
            # `.usage` stays None on a streamed completion unless this is set — the server only
            # sends the usage-bearing final chunk when asked. Omitting it would leave every
            # streamed call (the largest, priciest calls, by construction of this branch) priced
            # at zero with no error and no signal anything was wrong.
            with self._client.chat.completions.stream(
                stream_options={"include_usage": True}, **kwargs
            ) as stream:
                return cast(ChatCompletion, stream.get_final_completion())
        return cast(ChatCompletion, self._client.chat.completions.create(**kwargs))

    def _write_span(
        self,
        *,
        span_id: str,
        parent_span_id: str | None,
        step_index: int,
        latency_ms: int,
        model: str,
        stop_reason: str | None,
        usage: TokenUsage,
        micro_dollars: int,
        cassette_key: str,
        cassette_hit: bool,
        is_error: bool,
        kind: Literal["model_call", "judge_call"],
        rubric_version: str | None,
    ) -> None:
        """Build and append the span for one call.

        On an API error, `usage`/`micro_dollars` are zeroed rather than omitted — `ModelCallSpan`
        requires both (schema frozen in Task 2; changing it is an "ask first" action per
        SPEC-trace-core.md § Boundaries). A failed call before any response genuinely has no usage
        to report, and `is_error=True` is the discriminator that keeps a zeroed error span from
        ever being misread as a real free call.
        """
        common: dict[str, Any] = {
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "run_id": self._run_id,
            "step_index": step_index,
            "started_at": _now(),
            "latency_ms": latency_ms,
            "is_error": is_error,
            "model": model,
            "stop_reason": stop_reason,
            "usage": usage,
            "micro_dollars": micro_dollars,
            "cassette_key": cassette_key,
            "cassette_hit": cassette_hit,
        }
        if kind == "judge_call":
            assert rubric_version is not None  # enforced in create()
            self._writer.append(JudgeCallSpan(rubric_version=rubric_version, **common))
        else:
            self._writer.append(ModelCallSpan(**common))


def usage_from_completion(completion: ChatCompletion) -> TokenUsage:
    """Build a `TokenUsage` from the API's reported usage.

    `reasoning_tokens` is derived, not read from a dedicated field: live testing against Gemini
    3 (`gemini-3.8-flash`, 2026-09-23) found `usage.total_tokens` exceeding
    `prompt_tokens + completion_tokens` by 40-90%, with no `completion_tokens_details` populated
    to explain the gap — hidden thinking tokens billed but not itemized through the OpenAI-compat
    endpoint. Taking `max(0, api_total - prompt - completion)` recovers that gap without
    depending on a details field this provider doesn't fill in; it is exactly 0 for a provider
    whose reported total already equals prompt + completion.
    """
    if completion.usage is None:
        return TokenUsage(prompt_tokens=0, completion_tokens=0)
    prompt_tokens = completion.usage.prompt_tokens
    completion_tokens = completion.usage.completion_tokens
    reasoning_tokens = max(0, completion.usage.total_tokens - prompt_tokens - completion_tokens)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
