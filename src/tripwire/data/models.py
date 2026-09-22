"""Corpus schemas: threads, customers, orders, and the manifest that ties a generation run
together. See SPEC-synthetic-data.md § Corpus.

These are read-only data the agent's tools look up (Task 6) — this module has no opinion about
how the agent uses them, only what the data looks like.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CORPUS_SCHEMA_VERSION = 1

# Intent is a reporting-only label, never shown to the agent (SPEC-synthetic-data.md § Corpus).
Intent = Literal[
    "billing_question",
    "refund_request",
    "bug_report",
    "feature_request",
    "faq",
    "spam",
    "angry_escalation",
    "must_refuse",
]

INTENTS: tuple[Intent, ...] = (
    "billing_question",
    "refund_request",
    "bug_report",
    "feature_request",
    "faq",
    "spam",
    "angry_escalation",
    "must_refuse",
)


class Customer(BaseModel):
    """A customer record an agent tool can look up by id or email."""

    model_config = ConfigDict(frozen=True)

    customer_id: str
    name: str
    email: str
    plan: Literal["free", "starter", "pro", "enterprise"]
    tenure_days: int = Field(ge=0)


class Order(BaseModel):
    """An order record, referenced by some threads. Not every referenced order id exists —
    see `Thread.order_missing`."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    customer_id: str
    product: str
    amount_cents: int = Field(ge=0)
    status: Literal["fulfilled", "refunded", "pending", "cancelled"]
    ordered_at: str = Field(description="ISO 8601 date, synthetic — not wall-clock time")


class ThreadSender(BaseModel):
    """Who sent a thread. `kind='unknown'` means the sender resolves to no customer record —
    a deliberate case, not a gap (SPEC-synthetic-data.md: "a thread's sender is either a real
    customer or explicitly marked unknown")."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["known", "unknown"]
    email: str
    customer_id: str | None = None


class ThreadMessage(BaseModel):
    """One message in a thread, oldest first."""

    model_config = ConfigDict(frozen=True)

    message_id: str
    sender_email: str
    sent_at: str = Field(description="ISO 8601 datetime, synthetic")
    subject: str
    body: str
    is_quoted_reply: bool = False


class Thread(BaseModel):
    """One inbox thread: the unit the agent triages. `intent` is a reporting label only —
    SPEC-synthetic-data.md is explicit that the agent never sees it."""

    model_config = ConfigDict(frozen=True)

    thread_id: str
    intent: Intent
    sender: ThreadSender
    referenced_order_id: str | None = None
    order_missing: bool = False
    messages: tuple[ThreadMessage, ...]


class CorpusManifest(BaseModel):
    """Generation metadata, committed alongside the corpus so determinism is checkable without
    re-diffing three JSON files by hand."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = CORPUS_SCHEMA_VERSION
    seed: int
    generator_version: str
    thread_count: int = Field(ge=0)
    customer_count: int = Field(ge=0)
    order_count: int = Field(ge=0)
    content_hash: str
    intent_counts: dict[str, int]
    missing_order_thread_ids: tuple[str, ...]
    unknown_sender_thread_ids: tuple[str, ...]


class Corpus(BaseModel):
    """The full in-memory corpus, before it's split across `threads.json` / `customers.json` /
    `orders.json` / `manifest.json` on disk."""

    model_config = ConfigDict(frozen=True)

    manifest: CorpusManifest
    threads: tuple[Thread, ...]
    customers: tuple[Customer, ...]
    orders: tuple[Order, ...]
