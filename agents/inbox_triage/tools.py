"""Tool implementations: pure functions over `InboxState` plus its mutation log.

Every function returns a JSON-serializable dict shaped `{"ok": True, ...}` or
`{"ok": False, "error": "..."}` — a failure is a normal return value, never an exception
(tasks/todo.md Task 6 acceptance criterion). `run_tool` is the single dispatch entry point: it
validates raw (already-JSON-parsed) arguments against the matching schema from `schemas.py`
before calling the implementation, so a malformed tool call from the model becomes an error
result the model can see and correct, not a crash.

This module imports nothing from `tripwire.assertions` or `tripwire.judge` — enforced by
`tests/unit/test_boundaries.py`, not just this docstring.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from agents.inbox_triage.schemas import (
    AddLabelArgs,
    ArchiveArgs,
    DraftReplyArgs,
    EscalateArgs,
    GetThreadArgs,
    LookupCustomerArgs,
    LookupOrderArgs,
    SearchThreadsArgs,
    SnoozeArgs,
)
from agents.inbox_triage.state import InboxState

_SNIPPET_LENGTH = 160
_PREVIEW_LENGTH = 140


def search_threads(state: InboxState, args: SearchThreadsArgs) -> dict[str, Any]:
    """Substring search over subject, message bodies, and sender email. Never exposes
    `Thread.intent` — that field is a reporting-only label the agent must never see."""
    needle = args.query.strip().lower()
    if not needle:
        return {"ok": False, "error": "query must not be empty"}

    matches = []
    for thread in state.corpus.threads:
        haystack = " ".join(
            [thread.messages[0].subject, thread.sender.email]
            + [m.body for m in thread.messages]
        ).lower()
        if needle in haystack:
            runtime = state.runtime[thread.thread_id]
            matches.append(
                {
                    "thread_id": thread.thread_id,
                    "subject": thread.messages[0].subject,
                    "sender_email": thread.sender.email,
                    "snippet": thread.messages[0].body[:_SNIPPET_LENGTH],
                    "status": runtime.status,
                }
            )
    return {"ok": True, "count": len(matches), "results": matches}


def get_thread(state: InboxState, args: GetThreadArgs) -> dict[str, Any]:
    """Full transcript of one thread. Never exposes `intent`, `order_missing`, or any other
    ground-truth field — those exist for grading, not for the agent to read."""
    thread = state.thread(args.thread_id)
    if thread is None:
        return {"ok": False, "error": f"thread {args.thread_id!r} not found"}
    runtime = state.runtime[thread.thread_id]
    return {
        "ok": True,
        "thread_id": thread.thread_id,
        "sender_email": thread.sender.email,
        "status": runtime.status,
        "labels": list(runtime.labels),
        "messages": [
            {
                "sender_email": m.sender_email,
                "sent_at": m.sent_at,
                "subject": m.subject,
                "body": m.body,
            }
            for m in thread.messages
        ],
    }


def lookup_customer(state: InboxState, args: LookupCustomerArgs) -> dict[str, Any]:
    if (args.email is None) == (args.customer_id is None):
        return {"ok": False, "error": "provide exactly one of email or customer_id"}
    customer = (
        state.customer_by_email(args.email)
        if args.email is not None
        else state.customer_by_id(args.customer_id)  # type: ignore[arg-type]
    )
    if customer is None:
        identifier = args.email or args.customer_id
        return {"ok": False, "error": f"no customer found for {identifier!r}"}
    return {
        "ok": True,
        "customer_id": customer.customer_id,
        "name": customer.name,
        "email": customer.email,
        "plan": customer.plan,
        "tenure_days": customer.tenure_days,
    }


def lookup_order(state: InboxState, args: LookupOrderArgs) -> dict[str, Any]:
    """The tool that surfaces the corpus's deliberately-missing-order cases as a normal
    not-found result, exactly as a real order-lookup API would."""
    order = state.order(args.order_id)
    if order is None:
        return {"ok": False, "error": f"order {args.order_id!r} not found"}
    return {
        "ok": True,
        "order_id": order.order_id,
        "customer_id": order.customer_id,
        "product": order.product,
        "amount_cents": order.amount_cents,
        "status": order.status,
        "ordered_at": order.ordered_at,
    }


def add_label(state: InboxState, args: AddLabelArgs) -> dict[str, Any]:
    thread = state.thread(args.thread_id)
    if thread is None:
        return {"ok": False, "error": f"thread {args.thread_id!r} not found"}
    runtime = state.runtime[thread.thread_id]
    if args.label not in runtime.labels:
        runtime.labels = (*runtime.labels, args.label)
    return {"ok": True, "thread_id": thread.thread_id, "labels": list(runtime.labels)}


def draft_reply(state: InboxState, args: DraftReplyArgs) -> dict[str, Any]:
    thread = state.thread(args.thread_id)
    if thread is None:
        return {"ok": False, "error": f"thread {args.thread_id!r} not found"}
    if not args.body.strip():
        return {"ok": False, "error": "reply body must not be empty"}
    runtime = state.runtime[thread.thread_id]
    runtime.draft_reply = args.body
    return {
        "ok": True,
        "thread_id": thread.thread_id,
        "preview": args.body[:_PREVIEW_LENGTH],
    }


def escalate(state: InboxState, args: EscalateArgs) -> dict[str, Any]:
    return _set_status(state, args.thread_id, "escalated", args.reason)


def snooze(state: InboxState, args: SnoozeArgs) -> dict[str, Any]:
    return _set_status(state, args.thread_id, "snoozed", args.reason)


def archive(state: InboxState, args: ArchiveArgs) -> dict[str, Any]:
    return _set_status(state, args.thread_id, "archived", args.reason)


def _set_status(
    state: InboxState, thread_id: str, status: str, reason: str | None
) -> dict[str, Any]:
    thread = state.thread(thread_id)
    if thread is None:
        return {"ok": False, "error": f"thread {thread_id!r} not found"}
    runtime = state.runtime[thread.thread_id]
    runtime.status = status  # type: ignore[assignment]
    runtime.status_reason = reason
    return {"ok": True, "thread_id": thread.thread_id, "status": status, "reason": reason}


_ARGS_MODELS: dict[str, type[BaseModel]] = {
    "search_threads": SearchThreadsArgs,
    "get_thread": GetThreadArgs,
    "lookup_customer": LookupCustomerArgs,
    "lookup_order": LookupOrderArgs,
    "add_label": AddLabelArgs,
    "draft_reply": DraftReplyArgs,
    "escalate": EscalateArgs,
    "snooze": SnoozeArgs,
    "archive": ArchiveArgs,
}

_IMPLEMENTATIONS: dict[str, Callable[[InboxState, Any], dict[str, Any]]] = {
    "search_threads": search_threads,
    "get_thread": get_thread,
    "lookup_customer": lookup_customer,
    "lookup_order": lookup_order,
    "add_label": add_label,
    "draft_reply": draft_reply,
    "escalate": escalate,
    "snooze": snooze,
    "archive": archive,
}


def run_tool(state: InboxState, tool_name: str, raw_arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate `raw_arguments` (already JSON-parsed — never string-matched, per SPEC.md) against
    the matching schema, call the implementation, and log the action. Unknown tool names and
    schema-invalid arguments are error results, not exceptions."""
    args_model = _ARGS_MODELS.get(tool_name)
    if args_model is None:
        result: dict[str, Any] = {"ok": False, "error": f"unknown tool {tool_name!r}"}
        state.record(tool_name, raw_arguments, ok=False)
        return result

    try:
        args = args_model.model_validate(raw_arguments)
    except ValidationError as exc:
        result = {
            "ok": False,
            "error": f"invalid arguments for {tool_name}: {exc.error_count()} error(s)",
        }
        state.record(tool_name, raw_arguments, ok=False)
        return result

    result = _IMPLEMENTATIONS[tool_name](state, args)
    state.record(tool_name, raw_arguments, ok=result.get("ok", False))
    return result
