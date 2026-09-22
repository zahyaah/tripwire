"""The append-only JSONL trace store: `TraceWriter` writes, `TraceReader` reads back.

A trace is the only artifact `assertions`, `judge`, and `report` read — none of them touch a live
agent object. That boundary is what makes this file's read/write contract load-bearing: get it
wrong and every downstream number is reading garbage.

Reading a trace file is a system boundary (the file may be from a crashed process, hand-edited,
or written by an older harness version), so `TraceReader.load` validates every line rather than
trusting the JSON on disk — api-and-interface-design § "validate at boundaries".
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, TextIO

from pydantic import TypeAdapter, ValidationError

from tripwire.core.records import SCHEMA_VERSION, RunRecord, Span

_span_adapter: TypeAdapter[Span] = TypeAdapter(Span)


class UnsupportedSchemaVersionError(Exception):
    """A record's schema_version is newer than this build understands.

    Committed traces are never silently re-interpreted under a schema this code predates —
    that would let a stale reader misreport cost or routing without any signal it did so.
    """


class TraceCorruptError(Exception):
    """A trace line is malformed in a way that is not an in-progress-write truncation."""


def default_trace_path(run_id: str, runs_dir: Path = Path("runs")) -> Path:
    """The conventional location for a run's trace file: `runs/<run_id>/trace.jsonl`."""
    return runs_dir / run_id / "trace.jsonl"


class TraceWriter:
    """Appends a run record and its spans to a `trace.jsonl` file, one JSON object per line.

    Each `append` flushes immediately: a crashed process leaves a readable prefix, because the
    entire point of a trace is to still be useful after the thing it's tracing falls over.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO = self._path.open("a", encoding="utf-8")

    def write_run(self, run: RunRecord) -> None:
        self._write_line({"record_type": "run", **run.model_dump(mode="json")})

    def append(self, span: Span) -> None:
        self._write_line({"record_type": "span", **span.model_dump(mode="json")})

    def _write_line(self, obj: dict[str, Any]) -> None:
        self._file.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class TraceReader:
    """Loads a `trace.jsonl` file back into a typed `(RunRecord, list[Span])` pair."""

    @staticmethod
    def load(path: Path) -> tuple[RunRecord, list[Span]]:
        run: RunRecord | None = None
        spans: list[Span] = []
        lines = path.read_text(encoding="utf-8").splitlines()

        for index, line in enumerate(lines):
            is_last_line = index == len(lines) - 1
            if not line.strip():
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                if is_last_line:
                    warnings.warn(
                        f"{path}: truncated final line (byte-cut mid-write?) — "
                        f"loaded {len(spans)} span(s) from the prefix",
                        stacklevel=2,
                    )
                    break
                raise TraceCorruptError(f"{path}:{index + 1}: malformed JSON mid-file") from None

            record_type = obj.get("record_type")
            _check_schema_version(obj.get("schema_version"), path, index)
            body = {key: value for key, value in obj.items() if key != "record_type"}

            try:
                if record_type == "run":
                    run = RunRecord.model_validate(body)
                elif record_type == "span":
                    spans.append(_span_adapter.validate_python(body))
                else:
                    raise TraceCorruptError(
                        f"{path}:{index + 1}: unknown record_type {record_type!r}"
                    )
            except ValidationError as exc:
                if is_last_line:
                    warnings.warn(
                        f"{path}: truncated/invalid final record — "
                        f"loaded {len(spans)} span(s) from the prefix ({exc.error_count()} "
                        "validation error(s) on the last line)",
                        stacklevel=2,
                    )
                    break
                raise TraceCorruptError(f"{path}:{index + 1}: {exc}") from exc

        if run is None:
            raise TraceCorruptError(f"{path}: no run record found")
        return run, spans


def _check_schema_version(schema_version: object, path: Path, index: int) -> None:
    if schema_version is None:
        return
    if not isinstance(schema_version, int) or schema_version > SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"{path}:{index + 1}: schema_version {schema_version!r} is newer than this build "
            f"supports ({SCHEMA_VERSION})"
        )
