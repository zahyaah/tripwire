"""Each tool's happy path and one failure path (tasks/todo.md Task 6). No model in the loop —
tools are exercised directly against a small generated corpus."""

from __future__ import annotations

import pytest

from agents.inbox_triage.state import InboxState
from agents.inbox_triage.tools import run_tool
from tripwire.data import Customer, Thread, generate_corpus


@pytest.fixture
def state() -> InboxState:
    corpus = generate_corpus(seed=1337, thread_count=60)
    return InboxState(corpus)


def _known_thread_id(state: InboxState) -> str:
    return state.corpus.threads[0].thread_id


def _known_customer(state: InboxState) -> Customer:
    for thread in state.corpus.threads:
        if thread.sender.kind == "known":
            customer = state.customer_by_id(thread.sender.customer_id)  # type: ignore[arg-type]
            assert customer is not None
            return customer
    raise AssertionError("expected at least one known-sender thread at this corpus size")


def _missing_order_thread(state: InboxState) -> Thread:
    for thread in state.corpus.threads:
        if thread.order_missing:
            return thread
    raise AssertionError("expected at least one missing-order thread at this corpus size")


# --- search_threads ---------------------------------------------------------------------------


def test_search_threads_happy_path_finds_by_subject_word(state: InboxState) -> None:
    thread = state.corpus.threads[0]
    query_word = thread.messages[0].subject.split()[0]
    result = run_tool(state, "search_threads", {"query": query_word})
    assert result["ok"] is True
    assert any(r["thread_id"] == thread.thread_id for r in result["results"])


def test_search_threads_empty_query_is_an_error(state: InboxState) -> None:
    result = run_tool(state, "search_threads", {"query": "   "})
    assert result["ok"] is False
    assert "error" in result


def test_search_threads_never_leaks_intent(state: InboxState) -> None:
    result = run_tool(state, "search_threads", {"query": "e"})  # broad match
    assert result["ok"] is True
    for r in result["results"]:
        assert "intent" not in r


# --- get_thread --------------------------------------------------------------------------------


def test_get_thread_happy_path(state: InboxState) -> None:
    thread_id = _known_thread_id(state)
    result = run_tool(state, "get_thread", {"thread_id": thread_id})
    assert result["ok"] is True
    assert result["thread_id"] == thread_id
    assert len(result["messages"]) >= 1
    assert "intent" not in result


def test_get_thread_unknown_id_is_an_error(state: InboxState) -> None:
    result = run_tool(state, "get_thread", {"thread_id": "thr_does_not_exist"})
    assert result["ok"] is False
    assert "thr_does_not_exist" in result["error"]


# --- lookup_customer ----------------------------------------------------------------------------


def test_lookup_customer_happy_path_by_email(state: InboxState) -> None:
    customer = _known_customer(state)
    result = run_tool(state, "lookup_customer", {"email": customer.email})
    assert result["ok"] is True
    assert result["customer_id"] == customer.customer_id


def test_lookup_customer_unknown_email_is_an_error(state: InboxState) -> None:
    result = run_tool(state, "lookup_customer", {"email": "nobody@example.com"})
    assert result["ok"] is False


def test_lookup_customer_requires_exactly_one_identifier(state: InboxState) -> None:
    result = run_tool(state, "lookup_customer", {})
    assert result["ok"] is False
    both = run_tool(
        state, "lookup_customer", {"email": "a@example.com", "customer_id": "cus_00000"}
    )
    assert both["ok"] is False


# --- lookup_order --------------------------------------------------------------------------------


def test_lookup_order_happy_path(state: InboxState) -> None:
    order = state.corpus.orders[0]
    result = run_tool(state, "lookup_order", {"order_id": order.order_id})
    assert result["ok"] is True
    assert result["order_id"] == order.order_id


def test_lookup_order_missing_order_is_an_error(state: InboxState) -> None:
    thread = _missing_order_thread(state)
    result = run_tool(state, "lookup_order", {"order_id": thread.referenced_order_id})
    assert result["ok"] is False
    assert thread.referenced_order_id in result["error"]


# --- add_label -----------------------------------------------------------------------------------


def test_add_label_happy_path(state: InboxState) -> None:
    thread_id = _known_thread_id(state)
    result = run_tool(state, "add_label", {"thread_id": thread_id, "label": "refund"})
    assert result["ok"] is True
    assert "refund" in result["labels"]
    # idempotent: adding the same label twice doesn't duplicate it
    result2 = run_tool(state, "add_label", {"thread_id": thread_id, "label": "refund"})
    assert result2["labels"].count("refund") == 1


def test_add_label_unknown_thread_is_an_error(state: InboxState) -> None:
    result = run_tool(state, "add_label", {"thread_id": "thr_nope", "label": "refund"})
    assert result["ok"] is False


# --- draft_reply ---------------------------------------------------------------------------------


def test_draft_reply_happy_path(state: InboxState) -> None:
    thread_id = _known_thread_id(state)
    result = run_tool(
        state, "draft_reply", {"thread_id": thread_id, "body": "Thanks for reaching out."}
    )
    assert result["ok"] is True
    assert state.runtime[thread_id].draft_reply == "Thanks for reaching out."


def test_draft_reply_unknown_thread_is_an_error(state: InboxState) -> None:
    result = run_tool(state, "draft_reply", {"thread_id": "thr_nope", "body": "hi"})
    assert result["ok"] is False


# --- escalate / snooze / archive ------------------------------------------------------------------


def test_escalate_happy_path_sets_status(state: InboxState) -> None:
    thread_id = _known_thread_id(state)
    result = run_tool(state, "escalate", {"thread_id": thread_id, "reason": "needs a human"})
    assert result["ok"] is True
    assert state.runtime[thread_id].status == "escalated"


def test_escalate_unknown_thread_is_an_error(state: InboxState) -> None:
    result = run_tool(state, "escalate", {"thread_id": "thr_nope", "reason": "x"})
    assert result["ok"] is False


def test_snooze_happy_path_sets_status(state: InboxState) -> None:
    thread_id = _known_thread_id(state)
    result = run_tool(state, "snooze", {"thread_id": thread_id, "reason": "waiting on customer"})
    assert result["ok"] is True
    assert state.runtime[thread_id].status == "snoozed"


def test_archive_happy_path_sets_status(state: InboxState) -> None:
    thread_id = _known_thread_id(state)
    result = run_tool(state, "archive", {"thread_id": thread_id, "reason": "resolved"})
    assert result["ok"] is True
    assert state.runtime[thread_id].status == "archived"


# --- dispatch / validation ----------------------------------------------------------------------


def test_run_tool_unknown_tool_name_is_an_error_not_a_crash(state: InboxState) -> None:
    result = run_tool(state, "delete_everything", {})
    assert result["ok"] is False


def test_run_tool_invalid_arguments_are_an_error_not_a_crash(state: InboxState) -> None:
    thread_id = _known_thread_id(state)
    # get_thread requires thread_id; give it something schema-invalid instead.
    result = run_tool(state, "get_thread", {"thread_id": 12345})
    assert result["ok"] is False
    assert "invalid arguments" in result["error"]
    del thread_id  # unused, kept for readability of intent


def test_run_tool_logs_every_call(state: InboxState) -> None:
    thread_id = _known_thread_id(state)
    run_tool(state, "get_thread", {"thread_id": thread_id})
    run_tool(state, "get_thread", {"thread_id": "thr_nope"})
    assert len(state.action_log) == 2
    assert state.action_log[0].ok is True
    assert state.action_log[1].ok is False
    assert state.action_log[0].sequence == 0
    assert state.action_log[1].sequence == 1
