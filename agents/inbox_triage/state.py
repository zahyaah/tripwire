"""Runtime state for one triage session: the frozen corpus plus whatever the agent has done to it
so far (labels, drafts, status changes) and an append-only log of every tool call.

The corpus itself (`tripwire.data.Corpus`) is immutable — a golden case never wants the *input*
changing under it. Everything an agent tool does is layered on top, in `ThreadRuntimeState` and
`action_log`, so a run's effects are always attributable and a fresh `InboxState` per run means
no leakage between cases.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tripwire.data import Corpus, Customer, Order, Thread

ThreadStatus = Literal["open", "escalated", "snoozed", "archived"]


class ThreadRuntimeState(BaseModel):
    """What an agent has done to one thread. Starts empty; tools append to it."""

    labels: tuple[str, ...] = ()
    draft_reply: str | None = None
    status: ThreadStatus = "open"
    status_reason: str | None = None


class ActionLogEntry(BaseModel):
    """One tool call, for tests and later assertion matching against a recorded trace.

    `sequence` is a logical counter, not a timestamp — action ordering needs to be deterministic
    and testable, and nothing here needs wall-clock time to do that.
    """

    model_config = ConfigDict(frozen=True)

    sequence: int = Field(ge=0)
    tool_name: str
    arguments: dict[str, object]
    ok: bool


class InboxState:
    """Mutable session state wrapping one immutable `Corpus`. One instance per agent run."""

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self._threads_by_id: dict[str, Thread] = {t.thread_id: t for t in corpus.threads}
        self._customers_by_id: dict[str, Customer] = {
            c.customer_id: c for c in corpus.customers
        }
        self._customers_by_email: dict[str, Customer] = {
            c.email.lower(): c for c in corpus.customers
        }
        self._orders_by_id: dict[str, Order] = {o.order_id: o for o in corpus.orders}
        self.runtime: dict[str, ThreadRuntimeState] = {
            t.thread_id: ThreadRuntimeState() for t in corpus.threads
        }
        self.action_log: list[ActionLogEntry] = []

    def thread(self, thread_id: str) -> Thread | None:
        return self._threads_by_id.get(thread_id)

    def customer_by_id(self, customer_id: str) -> Customer | None:
        return self._customers_by_id.get(customer_id)

    def customer_by_email(self, email: str) -> Customer | None:
        return self._customers_by_email.get(email.lower())

    def order(self, order_id: str) -> Order | None:
        return self._orders_by_id.get(order_id)

    def record(self, tool_name: str, arguments: dict[str, object], ok: bool) -> None:
        self.action_log.append(
            ActionLogEntry(
                sequence=len(self.action_log),
                tool_name=tool_name,
                arguments=arguments,
                ok=ok,
            )
        )
