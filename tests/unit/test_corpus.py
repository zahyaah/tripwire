"""Corpus generator: determinism, referential integrity, and no-PII checks
(SPEC-synthetic-data.md § Corpus, tasks/todo.md Task 5)."""

from __future__ import annotations

from pathlib import Path

from tripwire.data import Corpus, generate_corpus, load_corpus, write_corpus
from tripwire.data.models import INTENTS

_RESERVED_DOMAIN_SUFFIXES = (".example.com", ".example.net", ".example.org")
_RESERVED_DOMAINS = ("example.com", "example.net", "example.org")


def _is_reserved_domain(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1]
    return domain in _RESERVED_DOMAINS or domain.endswith(_RESERVED_DOMAIN_SUFFIXES)


def test_same_seed_produces_byte_identical_content_hash() -> None:
    a = generate_corpus(seed=1337, thread_count=40)
    b = generate_corpus(seed=1337, thread_count=40)
    assert a.manifest.content_hash == b.manifest.content_hash
    assert a.threads == b.threads
    assert a.customers == b.customers
    assert a.orders == b.orders


def test_different_seeds_produce_different_content_hash() -> None:
    a = generate_corpus(seed=1337, thread_count=40)
    b = generate_corpus(seed=7, thread_count=40)
    assert a.manifest.content_hash != b.manifest.content_hash


def test_requested_thread_count_is_honored() -> None:
    corpus = generate_corpus(seed=1, thread_count=25)
    assert len(corpus.threads) == 25
    assert corpus.manifest.thread_count == 25


def test_every_sender_resolves_to_a_customer_or_is_marked_unknown() -> None:
    corpus = generate_corpus(seed=1337, thread_count=200)
    customer_ids = {c.customer_id for c in corpus.customers}
    for thread in corpus.threads:
        if thread.sender.kind == "known":
            assert thread.sender.customer_id in customer_ids
        else:
            assert thread.sender.customer_id is None


def test_every_referenced_order_either_exists_or_is_flagged_missing() -> None:
    corpus = generate_corpus(seed=1337, thread_count=200)
    order_ids = {o.order_id for o in corpus.orders}
    for thread in corpus.threads:
        if thread.referenced_order_id is None:
            continue
        exists = thread.referenced_order_id in order_ids
        assert exists != thread.order_missing, (
            f"{thread.thread_id}: order_missing={thread.order_missing} but "
            f"referenced_order_id exists={exists}"
        )


def test_manifest_missing_order_thread_ids_matches_the_flag_on_each_thread() -> None:
    corpus = generate_corpus(seed=1337, thread_count=200)
    flagged = {t.thread_id for t in corpus.threads if t.order_missing}
    assert set(corpus.manifest.missing_order_thread_ids) == flagged


def test_manifest_intent_counts_match_actual_thread_intents() -> None:
    corpus = generate_corpus(seed=1337, thread_count=200)
    actual: dict[str, int] = {intent: 0 for intent in INTENTS}
    for thread in corpus.threads:
        actual[thread.intent] += 1
    assert corpus.manifest.intent_counts == actual


def test_every_intent_in_the_taxonomy_appears_at_this_scale() -> None:
    # Not a hard requirement at every thread count, but at 400 (the project's target scale) every
    # intent should show up at least once, or the golden set (Task 8/11) has nothing to draw on.
    corpus = generate_corpus(seed=1337, thread_count=400)
    seen = {t.intent for t in corpus.threads}
    assert seen == set(INTENTS)


def test_no_real_looking_email_domains_anywhere() -> None:
    corpus = generate_corpus(seed=1337, thread_count=200)
    for customer in corpus.customers:
        assert _is_reserved_domain(customer.email), customer.email
    for thread in corpus.threads:
        assert _is_reserved_domain(thread.sender.email), thread.sender.email
        for message in thread.messages:
            assert _is_reserved_domain(message.sender_email), message.sender_email


def test_multi_message_threads_have_ordered_timestamps_and_quoting() -> None:
    corpus = generate_corpus(seed=1337, thread_count=200)
    multi = [t for t in corpus.threads if len(t.messages) > 1]
    assert multi, "expected at least one multi-message thread at this scale"
    for thread in multi:
        timestamps = [m.sent_at for m in thread.messages]
        assert timestamps == sorted(timestamps)
        assert thread.messages[0].is_quoted_reply is False
        for follow_up in thread.messages[1:]:
            assert follow_up.is_quoted_reply is True
            assert "wrote:" in follow_up.body


def test_write_then_load_round_trips_exactly(tmp_path: Path) -> None:
    corpus = generate_corpus(seed=1337, thread_count=30)
    write_corpus(corpus, tmp_path)
    loaded = load_corpus(tmp_path)
    assert loaded == corpus


def test_write_is_byte_identical_across_two_generations(tmp_path: Path) -> None:
    corpus_a = generate_corpus(seed=1337, thread_count=30)
    corpus_b = generate_corpus(seed=1337, thread_count=30)
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    write_corpus(corpus_a, dir_a)
    write_corpus(corpus_b, dir_b)
    for name in ("threads.json", "customers.json", "orders.json", "manifest.json"):
        assert (dir_a / name).read_bytes() == (dir_b / name).read_bytes(), name


def test_load_corpus_missing_manifest_raises_actionable_error(tmp_path: Path) -> None:
    try:
        load_corpus(tmp_path)
    except FileNotFoundError as exc:
        assert "gen-corpus" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_some_angry_escalation_threads_reveal_the_real_ask_in_the_last_message() -> None:
    # SPEC-synthetic-data.md: "at least a few multi-message threads where the last message
    # changes the intent." Thread.intent stays "angry_escalation" (the true label), but the
    # final message should read as a much smaller, calmer ask.
    corpus = generate_corpus(seed=1337, thread_count=400)
    reveal_markers = ("taking a breath", "isn't as urgent")
    reveals = [
        t
        for t in corpus.threads
        if t.intent == "angry_escalation"
        and len(t.messages) > 1
        and any(marker in t.messages[-1].body for marker in reveal_markers)
    ]
    assert reveals, "expected at least one angry_escalation thread with a revealed last message"


def test_bug_report_followups_elaborate_on_the_same_bug_not_a_new_one() -> None:
    corpus = generate_corpus(seed=1337, thread_count=400)
    multi_bug_threads = [
        t for t in corpus.threads if t.intent == "bug_report" and len(t.messages) > 1
    ]
    assert multi_bug_threads, "expected at least one multi-message bug_report thread"
    for thread in multi_bug_threads:
        first_body = thread.messages[0].body
        error = first_body.split('I get: "', 1)[1].split('"', 1)[0]
        for follow_up in thread.messages[1:]:
            assert error in follow_up.body, (
                f"{thread.thread_id}: follow-up doesn't reference the original error {error!r}"
            )


def test_corpus_is_frozen_and_hashable_end_to_end() -> None:
    # Sanity check that the pydantic models round-trip through frozen containers correctly —
    # tuples all the way down, not lists that would break `frozen=True`'s guarantee.
    corpus: Corpus = generate_corpus(seed=1337, thread_count=5)
    assert isinstance(corpus.threads, tuple)
    assert isinstance(corpus.threads[0].messages, tuple)
