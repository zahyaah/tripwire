"""ModelGateway: record -> replay round trip, cassette miss, error spans, no network in replay."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion

from tripwire.core.records import JudgeCallSpan, ModelCallSpan, RunRecord
from tripwire.core.store import TraceReader, TraceWriter
from tripwire.cost.prices import PriceEntry, UnknownModelError
from tripwire.llm.cassettes import CassetteStore
from tripwire.llm.gateway import (
    STREAMING_MAX_TOKENS_THRESHOLD,
    CassetteMissError,
    ModelGateway,
    ModelRequest,
)

_PRICED_TABLE = {
    "test-model": PriceEntry(
        model="test-model",
        as_of=date(2026, 1, 1),
        prompt_micro_dollars_per_million=1_000_000,
        completion_micro_dollars_per_million=2_000_000,
    )
}


def _completion(
    *, prompt_tokens: int = 10, completion_tokens: int = 5, finish_reason: str = "stop"
) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "logprobs": None,
                    "message": {"role": "assistant", "content": "hi", "tool_calls": None},
                }
            ],
            "created": 1234,
            "model": "test-model",
            "object": "chat.completion",
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    )


class _FakeStreamManager:
    """Duck-types the context manager `client.chat.completions.stream(...)` returns."""

    def __init__(self, response: ChatCompletion) -> None:
        self._response = response

    def __enter__(self) -> _FakeStreamManager:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def get_final_completion(self) -> ChatCompletion:
        return self._response


class _FakeCompletionsResource:
    """Duck-types `client.chat.completions` far enough for ModelGateway._call_api."""

    def __init__(
        self, response: ChatCompletion | None = None, error: Exception | None = None
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    def stream(self, **kwargs: Any) -> _FakeStreamManager:
        self.stream_calls.append(kwargs)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return _FakeStreamManager(self._response)


class _FakeChat:
    def __init__(self, completions: _FakeCompletionsResource) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _FakeCompletionsResource) -> None:
        self.chat = _FakeChat(completions)


def _write_run(writer: TraceWriter, run_id: str = "run_1") -> None:
    """`TraceReader.load` requires a run-record header line (Task 2) — write one before spans."""
    writer.write_run(
        RunRecord(
            run_id=run_id,
            agent_name="test-agent",
            model="test-model",
            prompt_hash="deadbeef",
            harness_version="0.1.0",
            llm_mode="live",
            started_at=datetime.now(UTC),
        )
    )


def _request(**overrides: Any) -> ModelRequest:
    base: dict[str, Any] = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
    }
    base.update(overrides)
    return ModelRequest(**base)


def test_record_then_replay_round_trips_content_usage_and_cost(tmp_path: Path) -> None:
    cassettes = CassetteStore(base_dir=tmp_path / "cassettes")
    response = _completion(prompt_tokens=100, completion_tokens=20)
    fake = _FakeCompletionsResource(response=response)
    client = _FakeClient(fake)

    record_trace = tmp_path / "record" / "trace.jsonl"
    with TraceWriter(record_trace) as writer:
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="record",
            client=client,  # type: ignore[arg-type]
            cassettes=cassettes,
            price_table=_PRICED_TABLE,
        )
        _write_run(writer)
        recorded = gateway.create(_request(), step_index=0, parent_span_id="sp_root")

    _run, recorded_spans = TraceReader.load(record_trace)
    recorded_span = recorded_spans[0]
    assert isinstance(recorded_span, ModelCallSpan)
    assert recorded_span.cassette_hit is False
    # prompt: 100 * 1 = 100 ; completion: 20 * 2 = 40
    assert recorded_span.micro_dollars == 140

    replay_trace = tmp_path / "replay" / "trace.jsonl"
    with TraceWriter(replay_trace) as writer:
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="replay",
            cassettes=cassettes,
            price_table=_PRICED_TABLE,
        )
        _write_run(writer)
        replayed = gateway.create(_request(), step_index=0, parent_span_id="sp_root")

    _run2, replayed_spans = TraceReader.load(replay_trace)
    replayed_span = replayed_spans[0]
    assert isinstance(replayed_span, ModelCallSpan)
    assert replayed_span.cassette_hit is True
    assert replayed_span.usage == recorded_span.usage
    assert replayed_span.micro_dollars == recorded_span.micro_dollars == 140
    assert replayed_span.latency_ms == recorded_span.latency_ms
    assert replayed.choices[0].message.content == recorded.choices[0].message.content
    assert fake.calls, "the fake API was actually called during record"


def test_a_modified_request_misses_the_cassette(tmp_path: Path) -> None:
    cassettes = CassetteStore(base_dir=tmp_path / "cassettes")
    fake = _FakeCompletionsResource(response=_completion())
    client = _FakeClient(fake)

    with TraceWriter(tmp_path / "record" / "trace.jsonl") as writer:
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="record",
            client=client,  # type: ignore[arg-type]
            cassettes=cassettes,
            price_table=_PRICED_TABLE,
        )
        gateway.create(_request(), step_index=0, parent_span_id=None)

    with TraceWriter(tmp_path / "replay" / "trace.jsonl") as writer:
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="replay",
            cassettes=cassettes,
            price_table=_PRICED_TABLE,
        )
        with pytest.raises(CassetteMissError) as exc_info:
            # Same model/messages shape but a different message body -> different key.
            gateway.create(
                _request(messages=[{"role": "user", "content": "a different question"}]),
                step_index=0,
                parent_span_id=None,
            )
        assert exc_info.value.cassette_key


def test_replay_never_touches_the_fake_client(tmp_path: Path) -> None:
    cassettes = CassetteStore(base_dir=tmp_path / "cassettes")
    fake = _FakeCompletionsResource(response=_completion())
    client = _FakeClient(fake)
    with TraceWriter(tmp_path / "record" / "trace.jsonl") as writer:
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="record",
            client=client,  # type: ignore[arg-type]
            cassettes=cassettes,
            price_table=_PRICED_TABLE,
        )
        gateway.create(_request(), step_index=0, parent_span_id=None)
    assert len(fake.calls) == 1

    with TraceWriter(tmp_path / "replay" / "trace.jsonl") as writer:
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="replay",
            cassettes=cassettes,
            price_table=_PRICED_TABLE,
        )
        gateway.create(_request(), step_index=0, parent_span_id=None)
    assert len(fake.calls) == 1  # unchanged — replay used the cassette, not the client


def test_live_mode_without_client_raises_immediately(tmp_path: Path) -> None:
    with (
        TraceWriter(tmp_path / "trace.jsonl") as writer,
        pytest.raises(ValueError, match="needs a client"),
    ):
        ModelGateway(run_id="run_1", writer=writer, mode="live", client=None)


def test_api_error_still_emits_an_is_error_span_before_raising(tmp_path: Path) -> None:
    error = openai.APIConnectionError(request=httpx2.Request("POST", "https://example.com"))
    fake = _FakeCompletionsResource(error=error)
    client = _FakeClient(fake)
    trace_path = tmp_path / "trace.jsonl"

    with TraceWriter(trace_path) as writer:
        gateway = ModelGateway(
            run_id="run_1", writer=writer, mode="live", client=client, price_table=_PRICED_TABLE  # type: ignore[arg-type]
        )
        _write_run(writer)
        with pytest.raises(openai.APIConnectionError):
            gateway.create(_request(), step_index=0, parent_span_id=None)

    _run, spans = TraceReader.load(trace_path)
    span = spans[0]
    assert isinstance(span, ModelCallSpan)
    assert span.is_error is True
    assert span.usage.prompt_tokens == 0
    assert span.micro_dollars == 0


def test_judge_call_requires_rubric_version(tmp_path: Path) -> None:
    fake = _FakeCompletionsResource(response=_completion())
    client = _FakeClient(fake)
    with TraceWriter(tmp_path / "trace.jsonl") as writer:
        gateway = ModelGateway(
            run_id="run_1", writer=writer, mode="live", client=client, price_table=_PRICED_TABLE  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="rubric_version"):
            gateway.create(_request(), step_index=0, parent_span_id=None, kind="judge_call")


def test_judge_call_writes_a_judge_call_span(tmp_path: Path) -> None:
    fake = _FakeCompletionsResource(response=_completion())
    client = _FakeClient(fake)
    trace_path = tmp_path / "trace.jsonl"
    with TraceWriter(trace_path) as writer:
        gateway = ModelGateway(
            run_id="run_1", writer=writer, mode="live", client=client, price_table=_PRICED_TABLE  # type: ignore[arg-type]
        )
        _write_run(writer)
        gateway.create(
            _request(), step_index=0, parent_span_id=None, kind="judge_call", rubric_version="v1"
        )
    _run, spans = TraceReader.load(trace_path)
    assert isinstance(spans[0], JudgeCallSpan)
    assert spans[0].rubric_version == "v1"


def test_max_tokens_is_the_param_sent_not_max_completion_tokens(tmp_path: Path) -> None:
    # NVIDIA's own sample code uses max_tokens (source-verified, see gateway.py module docstring).
    fake = _FakeCompletionsResource(response=_completion())
    client = _FakeClient(fake)
    with TraceWriter(tmp_path / "trace.jsonl") as writer:
        gateway = ModelGateway(
            run_id="run_1", writer=writer, mode="live", client=client, price_table=_PRICED_TABLE  # type: ignore[arg-type]
        )
        gateway.create(_request(max_tokens=100), step_index=0, parent_span_id=None)
    assert "max_tokens" in fake.calls[0]
    assert "max_completion_tokens" not in fake.calls[0]


def test_no_thinking_or_effort_parameter_is_ever_sent(tmp_path: Path) -> None:
    fake = _FakeCompletionsResource(response=_completion())
    client = _FakeClient(fake)
    with TraceWriter(tmp_path / "trace.jsonl") as writer:
        gateway = ModelGateway(
            run_id="run_1", writer=writer, mode="live", client=client, price_table=_PRICED_TABLE  # type: ignore[arg-type]
        )
        gateway.create(_request(), step_index=0, parent_span_id=None)
    sent = fake.calls[0]
    assert "thinking" not in sent
    assert "effort" not in sent
    assert "budget_tokens" not in sent


def test_streaming_path_requests_usage_via_stream_options(tmp_path: Path) -> None:
    # Regression for a verified bug: the openai SDK only populates `.usage` on a streamed
    # completion when stream_options={"include_usage": True} is sent. Without it every streamed
    # call — exactly the largest, most expensive calls, by construction of the threshold below —
    # would silently price at zero.
    fake = _FakeCompletionsResource(response=_completion(prompt_tokens=50, completion_tokens=30))
    client = _FakeClient(fake)
    with TraceWriter(tmp_path / "trace.jsonl") as writer:
        gateway = ModelGateway(
            run_id="run_1", writer=writer, mode="live", client=client, price_table=_PRICED_TABLE  # type: ignore[arg-type]
        )
        _write_run(writer)
        gateway.create(
            _request(max_tokens=STREAMING_MAX_TOKENS_THRESHOLD + 1),
            step_index=0,
            parent_span_id=None,
        )
    assert fake.stream_calls, "expected the streaming path to be used"
    assert fake.stream_calls[0]["stream_options"] == {"include_usage": True}
    _run, spans = TraceReader.load(tmp_path / "trace.jsonl")
    span = spans[0]
    assert isinstance(span, ModelCallSpan)
    assert span.usage.prompt_tokens == 50  # not silently zeroed


def test_length_finish_reason_error_still_emits_an_is_error_span(tmp_path: Path) -> None:
    # Regression for a verified bug: LengthFinishReasonError is a sibling of APIError (both
    # extend OpenAIError directly), not a subclass — a plain `except openai.APIError` misses it.
    # A strict-schema tool call truncated at max_tokens raises exactly this, and the call is
    # already billable, so it must still produce a span.
    error = openai.LengthFinishReasonError(completion=_completion(finish_reason="length"))
    fake = _FakeCompletionsResource(error=error)
    client = _FakeClient(fake)
    trace_path = tmp_path / "trace.jsonl"
    with TraceWriter(trace_path) as writer:
        gateway = ModelGateway(
            run_id="run_1", writer=writer, mode="live", client=client, price_table=_PRICED_TABLE  # type: ignore[arg-type]
        )
        _write_run(writer)
        with pytest.raises(openai.LengthFinishReasonError):
            gateway.create(_request(), step_index=0, parent_span_id=None)
    _run, spans = TraceReader.load(trace_path)
    span = spans[0]
    assert isinstance(span, ModelCallSpan)
    assert span.is_error is True


def test_unpriced_model_still_emits_a_span_with_real_usage_before_raising(tmp_path: Path) -> None:
    # Regression for a verified bug: price_call's UnknownModelError used to fire after the API
    # call succeeded but before _write_span ran, silently dropping the span (and, in record mode,
    # leaving an orphaned cassette with no matching trace entry).
    fake = _FakeCompletionsResource(response=_completion(prompt_tokens=42, completion_tokens=7))
    client = _FakeClient(fake)
    trace_path = tmp_path / "trace.jsonl"
    with TraceWriter(trace_path) as writer:
        gateway = ModelGateway(
            run_id="run_1", writer=writer, mode="live", client=client, price_table={}
        )
        _write_run(writer)
        with pytest.raises(UnknownModelError):
            gateway.create(_request(), step_index=0, parent_span_id=None)
    _run, spans = TraceReader.load(trace_path)
    span = spans[0]
    assert isinstance(span, ModelCallSpan)
    assert span.is_error is True
    assert span.usage.prompt_tokens == 42  # real usage, not zeroed — the call did succeed
    assert span.micro_dollars == 0
