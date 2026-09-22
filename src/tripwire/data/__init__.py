"""Synthetic corpus: schemas, generation, and file I/O. See SPEC-synthetic-data.md § Corpus."""

from __future__ import annotations

from tripwire.data.generator import GENERATOR_VERSION, generate_corpus
from tripwire.data.io import load_corpus, write_corpus
from tripwire.data.models import (
    CORPUS_SCHEMA_VERSION,
    INTENTS,
    Corpus,
    CorpusManifest,
    Customer,
    Intent,
    Order,
    Thread,
    ThreadMessage,
    ThreadSender,
)

__all__ = [
    "CORPUS_SCHEMA_VERSION",
    "GENERATOR_VERSION",
    "INTENTS",
    "Corpus",
    "CorpusManifest",
    "Customer",
    "Intent",
    "Order",
    "Thread",
    "ThreadMessage",
    "ThreadSender",
    "generate_corpus",
    "load_corpus",
    "write_corpus",
]
