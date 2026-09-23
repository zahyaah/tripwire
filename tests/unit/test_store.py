"""TraceWriter / TraceReader round-trip, crash tolerance, and schema-version guard."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tripwire.core.records import (
    SCHEMA_VERSION,
    AgentRunSpan,
    ModelCallSpan,
    RunRecord,
    TokenUsage,
    ToolCallSpan,
)
from tripwire.core.store import (
    TraceCorruptError,
    TraceReader,
    TraceWriter,
    UnsupportedSchemaVersionError,
    default_trace_path,
)


def _run(run_id: str = "run_test") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        agent_name="inbox_triage",
        model="gemini-3.8-flash",
        prompt_hash="deadbeef",
        harness_version="0.1.0",
        llm_mode="replay",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _model_call(step_index: int, parent: str) -> ModelCallSpan:
    return ModelCallSpan(
        span_id=f"sp_model_{step_index}",
        parent_span_id=parent,
        run_id="run_test",
        step_index=step_index,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        latency_ms=250,
        model="gemini-3.8-flash",
        stop_reason="tool_calls",
        usage=TokenUsage(prompt_tokens=500, completion_tokens=80),
        micro_dollars=1200,
        cassette_key="key-" + str(step_index),
        cassette_hit=True,
    )


def _tool_call(step_index: int, parent: str, span_id: str, tool_name: str) -> ToolCallSpan:
    return ToolCallSpan(
        span_id=span_id,
        parent_span_id=parent,
        run_id="run_test",
        step_index=step_index,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        latency_ms=5,
        tool_name=tool_name,
        arguments={"thread_id": "thr_1"},
        result_summary="ok",
        result_bytes=64,
    )


def _write_four_step_run_with_parallel_tools(path: Path) -> tuple[RunRecord, list[object]]:
    """A 4-step run where step 2 fires two tool calls in parallel (same step_index)."""
    run = _run()
    root = AgentRunSpan(
        span_id="sp_root",
        parent_span_id=None,
        run_id=run.run_id,
        step_index=0,
        started_at=run.started_at,
        latency_ms=900,
        step_count=4,
        total_micro_dollars=4800,
    )
    spans: list[object] = [root]
    spans.append(_model_call(0, "sp_root"))
    spans.append(_tool_call(0, "sp_model_0", "sp_tool_0", "get_thread"))
    spans.append(_model_call(1, "sp_root"))
    spans.append(_tool_call(1, "sp_model_1", "sp_tool_1a", "lookup_customer"))
    spans.append(_tool_call(1, "sp_model_1", "sp_tool_1b", "lookup_order"))
    spans.append(_model_call(2, "sp_root"))
    spans.append(_tool_call(2, "sp_model_2", "sp_tool_2", "draft_reply"))
    spans.append(_model_call(3, "sp_root"))

    with TraceWriter(path) as writer:
        writer.write_run(run)
        for span in spans:
            writer.append(span)  # type: ignore[arg-type]
    return run, spans


def test_round_trip_preserves_every_span_including_parallel_calls(tmp_path: Path) -> None:
    path = tmp_path / "run_test" / "trace.jsonl"
    run, spans = _write_four_step_run_with_parallel_tools(path)

    loaded_run, loaded_spans = TraceReader.load(path)

    assert loaded_run == run
    assert loaded_spans == spans
    step_1_spans = [s for s in loaded_spans if s.step_index == 1]  # type: ignore[attr-defined]
    assert len(step_1_spans) == 3  # one model_call + two parallel tool_calls


def test_default_trace_path_matches_convention() -> None:
    assert default_trace_path("run_abc", Path("runs")) == Path("runs/run_abc/trace.jsonl")


def test_writer_flushes_each_append_for_crash_safety(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.write_run(_run())
    writer.append(_model_call(0, None))  # type: ignore[arg-type]
    # No close() — simulate a crashed process. The file must still be readable.
    contents = path.read_text(encoding="utf-8")
    assert len(contents.splitlines()) == 2
    writer.close()


def test_truncated_final_line_is_tolerated_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    with TraceWriter(path) as writer:
        writer.write_run(_run())
        writer.append(_model_call(0, None))  # type: ignore[arg-type]
        writer.append(_tool_call(0, "sp_model_0", "sp_tool_0", "get_thread"))

    # Simulate a process killed mid-write: chop the last line in half.
    lines = path.read_text(encoding="utf-8").splitlines()
    truncated = "\n".join([*lines[:-1], lines[-1][: len(lines[-1]) // 2]])
    path.write_text(truncated, encoding="utf-8")

    with pytest.warns(UserWarning, match="truncated"):
        run, spans = TraceReader.load(path)

    assert run.run_id == "run_test"
    assert len(spans) == 1  # only the fully-written model_call span survives


def test_unknown_schema_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    run_obj = {"record_type": "run", **_run().model_dump(mode="json")}
    run_obj["schema_version"] = SCHEMA_VERSION + 1
    path.write_text(json.dumps(run_obj) + "\n", encoding="utf-8")

    with pytest.raises(UnsupportedSchemaVersionError):
        TraceReader.load(path)


def test_missing_run_record_raises(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    span_obj = {"record_type": "span", **_model_call(0, None).model_dump(mode="json")}
    path.write_text(json.dumps(span_obj) + "\n", encoding="utf-8")

    with pytest.raises(TraceCorruptError, match="no run record"):
        TraceReader.load(path)


def test_malformed_line_mid_file_raises_not_silently_skipped(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    run_line = json.dumps({"record_type": "run", **_run().model_dump(mode="json")})
    span_line = json.dumps({"record_type": "span", **_model_call(1, None).model_dump(mode="json")})
    path.write_text(f"{run_line}\nnot-json-at-all\n{span_line}\n", encoding="utf-8")

    with pytest.raises(TraceCorruptError, match="malformed JSON mid-file"):
        TraceReader.load(path)
