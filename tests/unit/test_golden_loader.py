"""Golden case loader: all committed cases load, and malformed fixtures produce specific,
actionable errors naming the file, case id, and field (tasks/todo.md Task 8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tripwire.core.golden import GoldenCaseError, load_golden_file, load_golden_set

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN_DIR = _REPO_ROOT / "data" / "golden"


def test_all_committed_cases_load() -> None:
    cases = load_golden_set(_GOLDEN_DIR)
    assert len(cases) == 8


def test_committed_case_ids_are_unique() -> None:
    cases = load_golden_set(_GOLDEN_DIR)
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))


def test_every_committed_case_has_at_least_one_required_assertion() -> None:
    cases = load_golden_set(_GOLDEN_DIR)
    for case in cases:
        has_assertion = (
            case.expected_tool_calls or case.forbidden_tools or case.max_calls
        ) or case.expected_outcome is not None
        assert has_assertion, case.case_id


def test_committed_cases_cover_the_required_intents() -> None:
    cases = load_golden_set(_GOLDEN_DIR)
    intents = {c.intent for c in cases}
    for required in ("faq", "refund_request", "angry_escalation", "spam", "must_refuse"):
        assert required in intents, f"missing a golden case for intent={required!r}"


def test_malformed_yaml_syntax_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("case_id: [unbalanced\n", encoding="utf-8")
    with pytest.raises(GoldenCaseError, match="invalid YAML") as exc_info:
        load_golden_file(path)
    assert str(path) in str(exc_info.value)


def test_missing_required_field_names_case_id_and_field(tmp_path: Path) -> None:
    path = tmp_path / "missing_field.yaml"
    path.write_text(
        "case_id: broken-case-01\n"
        "intent: faq\n"
        "input: {}\n"  # missing thread_id
        "expected_tool_calls:\n"
        "  - tool: get_thread\n",
        encoding="utf-8",
    )
    with pytest.raises(GoldenCaseError, match="broken-case-01") as exc_info:
        load_golden_file(path)
    assert "thread_id" in str(exc_info.value)


def test_case_with_no_assertions_at_all_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty_assertions.yaml"
    path.write_text(
        "case_id: vacuous-case-01\n"
        "intent: faq\n"
        "input:\n"
        "  thread_id: thr_00001\n",
        encoding="utf-8",
    )
    with pytest.raises(GoldenCaseError, match="vacuous-case-01") as exc_info:
        load_golden_file(path)
    assert "no assertion at all" in str(exc_info.value)


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "typo_field.yaml"
    path.write_text(
        "case_id: typo-case-01\n"
        "intent: faq\n"
        "input:\n"
        "  thread_id: thr_00001\n"
        "forbiden_tools: [escalate]\n",  # typo: should be forbidden_tools
        encoding="utf-8",
    )
    with pytest.raises(GoldenCaseError, match="typo-case-01"):
        load_golden_file(path)


def test_duplicate_case_id_across_files_is_rejected(tmp_path: Path) -> None:
    case_yaml = (
        "case_id: dup-case-01\n"
        "intent: faq\n"
        "input:\n"
        "  thread_id: thr_00001\n"
        "expected_tool_calls:\n"
        "  - tool: get_thread\n"
    )
    (tmp_path / "a.yaml").write_text(case_yaml, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(case_yaml, encoding="utf-8")
    with pytest.raises(GoldenCaseError, match="duplicate case_id"):
        load_golden_set(tmp_path)


def test_list_of_cases_in_one_file_all_load(tmp_path: Path) -> None:
    path = tmp_path / "grouped.yaml"
    path.write_text(
        "- case_id: grouped-01\n"
        "  intent: faq\n"
        "  input:\n"
        "    thread_id: thr_00001\n"
        "  expected_tool_calls:\n"
        "    - tool: get_thread\n"
        "- case_id: grouped-02\n"
        "  intent: faq\n"
        "  input:\n"
        "    thread_id: thr_00002\n"
        "  expected_tool_calls:\n"
        "    - tool: get_thread\n",
        encoding="utf-8",
    )
    cases = load_golden_file(path)
    assert [c.case_id for c in cases] == ["grouped-01", "grouped-02"]


def test_matcher_defaults_to_subset_when_unspecified(tmp_path: Path) -> None:
    path = tmp_path / "default_matcher.yaml"
    path.write_text(
        "case_id: default-matcher-01\n"
        "intent: faq\n"
        "input:\n"
        "  thread_id: thr_00001\n"
        "expected_tool_calls:\n"
        "  - tool: get_thread\n"
        "    args:\n"
        "      thread_id: { value: thr_00001 }\n",  # no `matcher:` key
        encoding="utf-8",
    )
    cases = load_golden_file(path)
    assert cases[0].expected_tool_calls[0].args["thread_id"].matcher == "subset"
