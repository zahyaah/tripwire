"""Cassette record/replay: the model-call boundary is where "deterministic CI, no API key" comes
from (SPEC.md § Assumptions #2).

Contract-first (api-and-interface-design): `compute_cassette_key` is a pure function of the
semantically relevant request — model, messages, tools, tool_choice, response_format — over a
canonicalized JSON encoding, so dict key order and whitespace never change the key. `CassetteStore`
is a thin, file-backed read/write pair with no other state.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CassetteRecord(BaseModel):
    """What gets stored for one recorded call: the raw response and the latency it took.

    Deliberately does *not* store `micro_dollars`: cost is re-derived from the restored `usage`
    via `price_call` every time a span is emitted (see gateway.py), live or replayed — a stored
    cost figure would go stale the moment `PRICE_TABLE` changes, and this way there is exactly
    one code path that computes cost, not two that can drift apart.
    """

    model_config = ConfigDict(frozen=True)

    completion: dict[str, Any] = Field(description="The raw ChatCompletion, as `.model_dump()`")
    latency_ms: int = Field(ge=0)


def _canonical_json(obj: Any) -> str:
    """Stable encoding: sorted keys, no insignificant whitespace, so equivalent requests hash
    identically regardless of dict construction order (SPEC-trace-core.md § Cassettes)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def compute_cassette_key(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    response_format: dict[str, Any] | None,
    max_tokens: int | None,
) -> str:
    """A stable SHA-256 over the fields that determine what the model would be asked.

    `max_tokens` is included: it is not merely a client-side cap that leaves the answer
    unaffected. A smaller `max_tokens` can truncate the completion and flip `finish_reason` to
    `"length"` — that is a different response to a different question, by this function's own
    stated test ("what determines what the model would be asked"), so two requests that differ
    only in `max_tokens` must not collide on the same cassette.

    Still excludes anything that varies per attempt without changing the question asked: request
    ids, timestamps, retry counts, and `stream` (a transport choice — replay must hit the same
    cassette however the call was made).
    """
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "response_format": response_format,
        "max_tokens": max_tokens,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class CorruptCassetteError(Exception):
    """A cassette file exists but doesn't parse as a `CassetteRecord`.

    Deliberately a different error from a *missing* cassette (`CassetteStore.load` returning
    `None`, which the gateway turns into `CassetteMissError`): the fix for a corrupt file is
    "delete and re-record", not "record for the first time" — collapsing the two into one
    raw `ValidationError` would leave a replay failure with no actionable message, breaking
    SPEC-trace-core.md's "prints the key... and the exact re-record command" contract.
    """


class CassetteStore:
    """File-backed cassette storage: one JSON file per key under a base directory.

    `base_dir` defaults to a path relative to the process's current working directory, not the
    repository root — a deliberate choice to keep this module free of any "find the repo root"
    logic of its own. Any caller that isn't already running from the repo root (a test runner
    with a different cwd, a script invoked from elsewhere) must pass an absolute `base_dir`
    explicitly; the CLI entry point (Task 1's `tripwire` command) is expected to do this.
    """

    def __init__(self, base_dir: Path = Path("fixtures/cassettes")) -> None:
        self._base_dir = base_dir

    def _path(self, key: str) -> Path:
        return self._base_dir / f"{key}.json"

    def load(self, key: str) -> CassetteRecord | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return CassetteRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise CorruptCassetteError(
                f"{path}: exists but doesn't parse as a cassette — delete it and re-record "
                "(`--mode record`), don't hand-edit it"
            ) from exc

    def save(self, key: str, record: CassetteRecord) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file and rename over the target: os.replace is atomic on both
        # POSIX and Windows, so a crash mid-write (or a concurrent `record` run touching the same
        # key) can never leave a half-written file for `load` to trip over.
        tmp_path = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
        tmp_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
