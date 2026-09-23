"""Judge rubric + structured-output call (tasks/todo.md Task 12): valid output parses, malformed
output raises rather than yielding partial scores, judge cost is separate from agent cost."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from openai.types.chat import ChatCompletion

from tripwire.core.records import AgentRunSpan, JudgeCallSpan, ModelCallSpan, RunRecord, TokenUsage
from tripwire.core.store import TraceReader, TraceWriter
from tripwire.cost.prices import PriceEntry
from tripwire.cost.rollup import rollup
from tripwire.data import generate_corpus
from tripwire.judge.judge import JudgeOutputError, judge_run
from tripwire.judge.rubric import RubricScore, rubric_response_format
from tripwire.llm.gateway import ModelGateway

_PRICE_TABLE = {
    "test-model": PriceEntry(
        model="test-model",
        as_of=date(2026, 1, 1),
        prompt_micro_dollars_per_million=1_000_000,
        completion_micro_dollars_per_million=2_000_000,
    )
}

_VALID_RUBRIC_JSON = json.dumps(
    {
        "reply_helpfulness": 4,
        "reply_helpfulness_rationale": "Addressed the billing question directly.",
        "tone_match": 5,
        "tone_match_rationale": "Calm and professional, matching a routine question.",
        "escalation_appropriate": True,
        "escalation_appropriate_rationale": "No escalation was needed here, and none happened.",
        "contains_unsupported_claim": False,
        "contains_unsupported_claim_rationale": "The reply only restates verified account info.",
    }
)


def _completion(content: str | None) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-judge",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "logprobs": None,
                    "message": {"role": "assistant", "content": content, "tool_calls": None},
                }
            ],
            "created": 1,
            "model": "test-model",
            "object": "chat.completion",
            "usage": {"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260},
        }
    )


class _FakeCompletionsResource:
    def __init__(self, response: ChatCompletion) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, resource: _FakeCompletionsResource) -> None:
        self.chat = type("Chat", (), {"completions": resource})()


def test_valid_judge_output_parses_into_rubric_score(tmp_path: Path) -> None:
    corpus = generate_corpus(seed=1, thread_count=10)
    thread_id = corpus.threads[0].thread_id
    resource = _FakeCompletionsResource(_completion(_VALID_RUBRIC_JSON))
    client = _FakeClient(resource)
    trace_path = tmp_path / "trace.jsonl"

    with TraceWriter(trace_path) as writer:
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="live",
            client=client,  # type: ignore[arg-type]
            price_table=_PRICE_TABLE,
        )
        score = judge_run(
            gateway=gateway,
            model="test-model",
            thread_id=thread_id,
            spans=[],
            corpus=corpus,
            step_index=0,
            parent_span_id=None,
        )

    assert isinstance(score, RubricScore)
    assert score.reply_helpfulness == 4
    assert score.contains_unsupported_claim is False

    # The gateway request actually used response_format (structured output), not a
    # prompt-only "please answer JSON" instruction.
    assert resource.calls[0]["response_format"] == rubric_response_format()


def test_malformed_judge_output_raises_not_partial_score(tmp_path: Path) -> None:
    corpus = generate_corpus(seed=1, thread_count=10)
    thread_id = corpus.threads[0].thread_id
    # Missing required fields and a wrong type — should fail pydantic validation entirely.
    bad_json = json.dumps({"reply_helpfulness": "not-a-number"})
    resource = _FakeCompletionsResource(_completion(bad_json))
    client = _FakeClient(resource)
    trace_path = tmp_path / "trace.jsonl"

    with TraceWriter(trace_path) as writer:
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="live",
            client=client,  # type: ignore[arg-type]
            price_table=_PRICE_TABLE,
        )
        with pytest.raises(JudgeOutputError, match="did not match the rubric schema"):
            judge_run(
                gateway=gateway,
                model="test-model",
                thread_id=thread_id,
                spans=[],
                corpus=corpus,
                step_index=0,
                parent_span_id=None,
            )


def test_judge_output_with_no_content_raises(tmp_path: Path) -> None:
    corpus = generate_corpus(seed=1, thread_count=10)
    thread_id = corpus.threads[0].thread_id
    resource = _FakeCompletionsResource(_completion(None))
    client = _FakeClient(resource)
    trace_path = tmp_path / "trace.jsonl"

    with TraceWriter(trace_path) as writer:
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="live",
            client=client,  # type: ignore[arg-type]
            price_table=_PRICE_TABLE,
        )
        with pytest.raises(JudgeOutputError, match="no content"):
            judge_run(
                gateway=gateway,
                model="test-model",
                thread_id=thread_id,
                spans=[],
                corpus=corpus,
                step_index=0,
                parent_span_id=None,
            )


def test_judge_call_writes_a_judge_call_span_not_model_call(tmp_path: Path) -> None:
    corpus = generate_corpus(seed=1, thread_count=10)
    thread_id = corpus.threads[0].thread_id
    resource = _FakeCompletionsResource(_completion(_VALID_RUBRIC_JSON))
    client = _FakeClient(resource)
    trace_path = tmp_path / "trace.jsonl"

    with TraceWriter(trace_path) as writer:
        writer.write_run(
            RunRecord(
                run_id="run_1",
                agent_name="test-agent",
                model="test-model",
                prompt_hash="deadbeef",
                harness_version="0.1.0",
                llm_mode="live",
                started_at=datetime.now(UTC),
            )
        )
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="live",
            client=client,  # type: ignore[arg-type]
            price_table=_PRICE_TABLE,
        )
        judge_run(
            gateway=gateway,
            model="test-model",
            thread_id=thread_id,
            spans=[],
            corpus=corpus,
            step_index=0,
            parent_span_id="sp_root",
        )
    _run, spans = TraceReader.load(trace_path)
    assert len(spans) == 1
    assert isinstance(spans[0], JudgeCallSpan)
    assert spans[0].rubric_version == "v1"


def test_judge_cost_is_separate_from_agent_cost_in_rollup(tmp_path: Path) -> None:
    """Verification bullet: judge cost appears in the run rollup separately from agent cost."""
    corpus = generate_corpus(seed=1, thread_count=10)
    thread_id = corpus.threads[0].thread_id
    resource = _FakeCompletionsResource(_completion(_VALID_RUBRIC_JSON))
    client = _FakeClient(resource)
    trace_path = tmp_path / "trace.jsonl"

    with TraceWriter(trace_path) as writer:
        writer.write_run(
            RunRecord(
                run_id="run_1",
                agent_name="test-agent",
                model="test-model",
                prompt_hash="deadbeef",
                harness_version="0.1.0",
                llm_mode="live",
                started_at=datetime.now(UTC),
            )
        )
        writer.append(
            AgentRunSpan(
                span_id="sp_root",
                parent_span_id=None,
                run_id="run_1",
                step_index=0,
                started_at=datetime.now(UTC),
                latency_ms=1,
                step_count=1,
                total_micro_dollars=0,
            )
        )
        writer.append(
            ModelCallSpan(
                span_id="sp_model",
                parent_span_id="sp_root",
                run_id="run_1",
                step_index=0,
                started_at=datetime.now(UTC),
                latency_ms=1,
                model="test-model",
                stop_reason="stop",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
                micro_dollars=20,
                cassette_key="k",
                cassette_hit=True,
            )
        )
        gateway = ModelGateway(
            run_id="run_1",
            writer=writer,
            mode="live",
            client=client,  # type: ignore[arg-type]
            price_table=_PRICE_TABLE,
        )
        judge_run(
            gateway=gateway,
            model="test-model",
            thread_id=thread_id,
            spans=[],
            corpus=corpus,
            step_index=0,
            parent_span_id="sp_root",
        )

    _run, spans = TraceReader.load(trace_path)
    rolled = rollup("run_1", spans)
    assert rolled.agent_micro_dollars == 20
    # judge: prompt=200*1 + completion=60*2 = 200 + 120 = 320
    assert rolled.judge_micro_dollars == 320
    assert rolled.total_micro_dollars == 20 + 320
