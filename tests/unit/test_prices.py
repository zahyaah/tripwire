"""Hand-computed price cases: exact division, round-down, round-up, unknown model, zero-price."""

from __future__ import annotations

from datetime import date

import pytest

from tripwire.core.records import TokenUsage
from tripwire.cost.prices import PRICE_TABLE, PriceEntry, UnknownModelError, price_call


def _table(prompt_rate: int, completion_rate: int) -> dict[str, PriceEntry]:
    return {
        "test/priced-model": PriceEntry(
            model="test/priced-model",
            as_of=date(2026, 1, 1),
            prompt_micro_dollars_per_million=prompt_rate,
            completion_micro_dollars_per_million=completion_rate,
        )
    }


def test_exact_division_needs_no_rounding() -> None:
    # rate = 1_000_000 micro-$/M tokens ($1/M). 250 tokens * 1_000_000 / 1_000_000 = 250 exactly.
    table = _table(prompt_rate=1_000_000, completion_rate=1_000_000)
    usage = TokenUsage(prompt_tokens=250, completion_tokens=0)
    assert price_call("test/priced-model", usage, table=table) == 250


def test_fraction_below_half_rounds_down() -> None:
    # rate = 1 micro-$/M tokens. 400_000 tokens -> numerator 400_000 -> (400_000+500_000)//1e6 = 0.
    table = _table(prompt_rate=1, completion_rate=0)
    usage = TokenUsage(prompt_tokens=400_000, completion_tokens=0)
    assert price_call("test/priced-model", usage, table=table) == 0


def test_fraction_at_half_rounds_up() -> None:
    # rate = 1 micro-$/M tokens. 600_000 tokens -> numerator 600_000 -> (600_000+500_000)//1e6 = 1.
    table = _table(prompt_rate=1, completion_rate=0)
    usage = TokenUsage(prompt_tokens=600_000, completion_tokens=0)
    assert price_call("test/priced-model", usage, table=table) == 1


def test_prompt_and_completion_rates_are_independent() -> None:
    table = _table(prompt_rate=5_000_000, completion_rate=25_000_000)
    usage = TokenUsage(prompt_tokens=12_345, completion_tokens=678)
    # prompt: 12_345 * 5 = 61_725 ; completion: 678 * 25 = 16_950
    assert price_call("test/priced-model", usage, table=table) == 61_725 + 16_950


def test_rounding_happens_once_on_the_summed_cost_not_per_component() -> None:
    # rate = 400_000 micro-$/M for both. 1 prompt token + 1 completion token: each component's
    # exact value is 0.4 micro-dollars. Rounded independently, each floors to 0 (total 0) — the
    # bug this test guards against. Rounded once on the sum (0.4 + 0.4 = 0.8), it rounds up to 1.
    table = _table(prompt_rate=400_000, completion_rate=400_000)
    usage = TokenUsage(prompt_tokens=1, completion_tokens=1)
    assert price_call("test/priced-model", usage, table=table) == 1


def test_explicit_empty_table_raises_rather_than_falling_back_to_production_table() -> None:
    # `table={}` must mean "price against nothing", not "no table given" — an empty dict is
    # falsy, so a naive `table or PRICE_TABLE` would silently substitute the production table.
    usage = TokenUsage(prompt_tokens=1, completion_tokens=1)
    with pytest.raises(UnknownModelError):
        price_call("nvidia/nemotron-3.5-lightning", usage, table={})


def test_unknown_model_raises() -> None:
    usage = TokenUsage(prompt_tokens=100, completion_tokens=10)
    with pytest.raises(UnknownModelError, match="no price table entry"):
        price_call("totally/unknown-model", usage)


def test_known_zero_price_model_prices_at_zero_without_raising() -> None:
    usage = TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert price_call("nvidia/nemotron-3.5-lightning", usage, table=PRICE_TABLE) == 0


def test_billable_false_with_nonzero_rate_is_rejected() -> None:
    with pytest.raises(ValueError, match="billable=False but a nonzero rate"):
        PriceEntry(
            model="bad-entry",
            as_of=date(2026, 1, 1),
            prompt_micro_dollars_per_million=1,
            completion_micro_dollars_per_million=0,
            billable=False,
        )
