"""Cassette key stability and file-backed store round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from tripwire.llm.cassettes import (
    CassetteRecord,
    CassetteStore,
    CorruptCassetteError,
    compute_cassette_key,
)


def _key(**overrides: object) -> str:
    base: dict[str, object] = {
        "model": "gemini-3-flash-preview",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": None,
        "tool_choice": None,
        "response_format": None,
        "max_tokens": None,
    }
    base.update(overrides)
    return compute_cassette_key(**base)  # type: ignore[arg-type]


def test_key_is_stable_under_dict_reordering() -> None:
    a = compute_cassette_key(
        model="m",
        messages=[{"role": "user", "content": "hi", "extra": 1}],
        tools=None,
        tool_choice=None,
        response_format=None,
        max_tokens=None,
    )
    b = compute_cassette_key(
        model="m",
        messages=[{"extra": 1, "content": "hi", "role": "user"}],
        tools=None,
        tool_choice=None,
        response_format=None,
        max_tokens=None,
    )
    assert a == b


def test_key_changes_when_system_prompt_changes() -> None:
    a = _key(messages=[{"role": "system", "content": "v1"}])
    b = _key(messages=[{"role": "system", "content": "v2"}])
    assert a != b


def test_key_changes_when_tool_schema_changes() -> None:
    a = _key(tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}])
    b = _key(tools=[{"type": "function", "function": {"name": "y", "parameters": {}}}])
    assert a != b


def test_key_changes_when_max_tokens_changes() -> None:
    # A smaller max_tokens can truncate the completion and flip finish_reason to "length" — a
    # different response, so it must not collide with a request that differs only in this field.
    a = _key(max_tokens=256)
    b = _key(max_tokens=8000)
    assert a != b


def test_key_ignores_stream_because_its_not_a_parameter_at_all() -> None:
    # compute_cassette_key has no `stream` parameter — a transport choice, not part of the
    # question asked. Calling it twice with identical inputs must hash identically regardless.
    a = _key()
    b = _key()
    assert a == b


def test_store_round_trip(tmp_path: Path) -> None:
    store = CassetteStore(base_dir=tmp_path)
    record = CassetteRecord(completion={"id": "abc", "choices": []}, latency_ms=123)
    store.save("some-key", record)
    loaded = store.load("some-key")
    assert loaded == record


def test_store_missing_key_returns_none(tmp_path: Path) -> None:
    store = CassetteStore(base_dir=tmp_path)
    assert store.load("nope") is None


def test_store_corrupt_file_raises_corrupt_cassette_error_not_raw_validation_error(
    tmp_path: Path,
) -> None:
    store = CassetteStore(base_dir=tmp_path)
    (tmp_path / "broken-key.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CorruptCassetteError, match="broken-key"):
        store.load("broken-key")


def test_store_save_is_atomic_no_leftover_temp_file(tmp_path: Path) -> None:
    store = CassetteStore(base_dir=tmp_path)
    store.save("k", CassetteRecord(completion={}, latency_ms=1))
    leftovers = list(tmp_path.glob("*.tmp-*"))
    assert leftovers == []
