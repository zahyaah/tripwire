"""The manual agent loop: step/span accounting, budget termination, and a real record -> replay
round trip driven end to end through run_agent (tasks/todo.md Task 7)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletion

from agents.inbox_triage.loop import LoopConfig, run_agent
from agents.inbox_triage.state import InboxState
from tripwire.core.records import AgentRunSpan, ModelCallSpan, ToolCallSpan
from tripwire.core.store import TraceReader, TraceWriter
from tripwire.cost.prices import PriceEntry
from tripwire.data import generate_corpus
from tripwire.llm.cassettes import CassetteStore
from tripwire.llm.gateway import ModelGateway

_PRICE_TABLE = {
    "test-model": PriceEntry(
        model="test-model",
        as_of=date(2026, 1, 1),
        prompt_micro_dollars_per_million=1_000,
        completion_micro_dollars_per_million=2_000,
    )
}


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
    """Returns completions from a fixed script, in call order — ignores request content, so it
    works identically whether the loop's exact message-building changes shape slightly."""

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


def _run(
    *,
    mode: str,
    client: Any,
    cassettes: CassetteStore,
    trace_path: Path,
    run_id: str,
    thread_id: str,
    max_steps: int = 8,
) -> tuple[Any, Any]:
    corpus = generate_corpus(seed=1, thread_count=10)
    state = InboxState(corpus)
    config = LoopConfig(model="test-model", max_steps=max_steps)
    with TraceWriter(trace_path) as writer:
        gateway = ModelGateway(
            run_id=run_id,
            writer=writer,
            mode=mode,  # type: ignore[arg-type]
            client=client,
            cassettes=cassettes,
            price_table=_PRICE_TABLE,
        )
        result = run_agent(
            thread_id=thread_id,
            state=state,
            gateway=gateway,
            run_id=run_id,
            writer=writer,
            config=config,
        )
    run, spans = TraceReader.load(trace_path)
    return result, (run, spans)


def test_four_step_run_produces_expected_span_shape(tmp_path: Path) -> None:
    thread_id = "thr_00000"
    script = [
        _completion(
            finish_reason="tool_calls",
            tool_calls=[_tool_call("call_0", "get_thread", {"thread_id": thread_id})],
        ),
        _completion(
            finish_reason="tool_calls",
            tool_calls=[
                _tool_call("call_1", "add_label", {"thread_id": thread_id, "label": "faq"})
            ],
        ),
        _completion(
            finish_reason="tool_calls",
            tool_calls=[
                _tool_call(
                    "call_2", "draft_reply", {"thread_id": thread_id, "body": "Thanks!"}
                )
            ],
        ),
        _completion(finish_reason="stop", content="Handled: labeled and drafted a reply."),
    ]
    client = _ScriptedClient(script)
    cassettes = CassetteStore(base_dir=tmp_path / "cassettes")

    result, (run, spans) = _run(
        mode="record",
        client=client,
        cassettes=cassettes,
        trace_path=tmp_path / "trace.jsonl",
        run_id="run_1",
        thread_id=thread_id,
    )

    assert result.outcome == "completed"
    assert result.step_count == 4
    assert run.outcome == "completed"
    # Regression: llm_mode must reflect what the gateway actually did, not a hardcoded guess.
    assert run.llm_mode == "record"

    agent_run_spans = [s for s in spans if isinstance(s, AgentRunSpan)]
    model_call_spans = [s for s in spans if isinstance(s, ModelCallSpan)]
    tool_call_spans = [s for s in spans if isinstance(s, ToolCallSpan)]

    assert len(agent_run_spans) == 1
    assert len(model_call_spans) == 4
    assert len(tool_call_spans) == 3  # one per tool_calls step; the final "stop" step has none

    root_span_id = agent_run_spans[0].span_id
    assert all(s.parent_span_id == root_span_id for s in model_call_spans)
    assert all(s.parent_span_id == root_span_id for s in tool_call_spans)
    assert [s.step_index for s in model_call_spans] == [0, 1, 2, 3]
    assert [s.step_index for s in tool_call_spans] == [0, 1, 2]
    assert tool_call_spans[0].tool_name == "get_thread"
    assert tool_call_spans[1].tool_name == "add_label"
    assert tool_call_spans[2].tool_name == "draft_reply"
    assert all(not s.is_error for s in tool_call_spans)


def test_record_then_replay_round_trip_through_the_full_loop(tmp_path: Path) -> None:
    thread_id = "thr_00000"
    script = [
        _completion(
            finish_reason="tool_calls",
            tool_calls=[_tool_call("call_0", "get_thread", {"thread_id": thread_id})],
        ),
        _completion(finish_reason="stop", content="Done."),
    ]
    cassettes = CassetteStore(base_dir=tmp_path / "cassettes")

    recorded_result, (_recorded_run, recorded_spans) = _run(
        mode="record",
        client=_ScriptedClient(script),
        cassettes=cassettes,
        trace_path=tmp_path / "record" / "trace.jsonl",
        run_id="run_record",
        thread_id=thread_id,
    )

    # Replay uses the SAME cassette dir but no client at all — a real API call here would raise
    # (mode="replay" doesn't require one), proving nothing but the cassette was consulted.
    replayed_result, (_replayed_run, replayed_spans) = _run(
        mode="replay",
        client=None,
        cassettes=cassettes,
        trace_path=tmp_path / "replay" / "trace.jsonl",
        run_id="run_replay",
        thread_id=thread_id,
    )

    assert replayed_result.outcome == recorded_result.outcome == "completed"
    assert _recorded_run.llm_mode == "record"
    assert _replayed_run.llm_mode == "replay"
    assert replayed_result.final_message == recorded_result.final_message == "Done."
    recorded_model_calls = [s for s in recorded_spans if isinstance(s, ModelCallSpan)]
    replayed_model_calls = [s for s in replayed_spans if isinstance(s, ModelCallSpan)]
    assert len(replayed_model_calls) == len(recorded_model_calls) == 2
    assert all(not s.cassette_hit for s in recorded_model_calls)
    assert all(s.cassette_hit for s in replayed_model_calls)


def test_infinite_tool_loop_terminates_at_the_step_budget(tmp_path: Path) -> None:
    thread_id = "thr_00000"
    # Every call requests another tool call — never "stop" — scripted to repeat forever via
    # _ScriptedCompletionsResource clamping to the last script entry.
    script = [
        _completion(
            finish_reason="tool_calls",
            tool_calls=[_tool_call("call_x", "get_thread", {"thread_id": thread_id})],
        )
    ]
    client = _ScriptedClient(script)
    cassettes = CassetteStore(base_dir=tmp_path / "cassettes")

    result, (run, spans) = _run(
        mode="record",
        client=client,
        cassettes=cassettes,
        trace_path=tmp_path / "trace.jsonl",
        run_id="run_1",
        thread_id=thread_id,
        max_steps=3,
    )

    assert result.outcome == "budget_exceeded"
    assert result.step_count == 3
    assert run.outcome == "budget_exceeded"
    model_call_spans = [s for s in spans if isinstance(s, ModelCallSpan)]
    assert len(model_call_spans) == 3  # never a 4th call once the budget is hit
    assert client.chat.completions.call_count == 3


def test_cost_budget_terminates_the_run(tmp_path: Path) -> None:
    thread_id = "thr_00000"
    script = [
        _completion(
            finish_reason="tool_calls",
            tool_calls=[_tool_call("call_x", "get_thread", {"thread_id": thread_id})],
        )
    ]
    client = _ScriptedClient(script)
    cassettes = CassetteStore(base_dir=tmp_path / "cassettes")
    corpus = generate_corpus(seed=1, thread_count=10)
    state = InboxState(corpus)
    # Each call costs: 50*1_000/1e6 + 10*2_000/1e6 rounded = 0 (tiny numbers) -> use a higher
    # rate table instead so the budget actually bites within a couple of calls.
    table = {
        "test-model": PriceEntry(
            model="test-model",
            as_of=date(2026, 1, 1),
            prompt_micro_dollars_per_million=1_000_000_000,
            completion_micro_dollars_per_million=1_000_000_000,
        )
    }
    trace_path = tmp_path / "trace.jsonl"
    with TraceWriter(trace_path) as writer:
        gateway = ModelGateway(
            run_id="run_1", writer=writer, mode="record", client=client, cassettes=cassettes,
            price_table=table,
        )
        result = run_agent(
            thread_id=thread_id,
            state=state,
            gateway=gateway,
            run_id="run_1",
            writer=writer,
            config=LoopConfig(model="test-model", max_steps=100, max_micro_dollars=1),
        )
    assert result.outcome == "budget_exceeded"
    assert result.step_count == 1  # first call alone already blows the $0.000001 budget
