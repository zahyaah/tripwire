"""Price a model call from reported usage against a versioned price table.

Money is integer micro-dollars (1 micro-dollar = 1e-6 USD) end to end, never a float — see
SPEC.md § Code Style. Rates are stored as micro-dollars per million tokens, also an integer, so
`price_call` never introduces a floating-point rounding step of its own.

Per SPEC-trace-core.md § Cost: an *unknown* model raises (a silent zero would turn a real cost
regression into a passing build); a *known* free-tier/preview model is an explicit zero-price
row, which is a documented fact about that model, not a gap in the table.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

from tripwire.core.records import TokenUsage


class UnknownModelError(Exception):
    """`price_call` was asked to price a model with no entry in the table.

    Raised even when the caller "knows" the call must have cost something — an unpriced model is
    a gap in the table, not a signal to guess.
    """


class PriceEntry(BaseModel):
    """One priced (or explicitly free) model, as of a given date.

    `billable=False` marks a *known* free-tier/preview model (SPEC.md Open Question 7) — distinct
    from the model being absent from the table entirely, which is `UnknownModelError`.

    Known limitation: `as_of` records when a rate was published but is not consulted by
    `price_call` — `PRICE_TABLE` is keyed by model name only, so there is exactly one live rate
    per model at a time. If the provider revises a rate, updating the entry in place reprices any
    *future* `price_call` correctly, but re-deriving cost for *already-recorded* spans (whose
    `micro_dollars` was computed at call time and stored on the span, per SPEC-trace-core.md
    § Contracts) would silently use the new rate rather than the one billed. `rollup` never hits
    this — it sums the stored `span.micro_dollars`, not a fresh `price_call` — but a future tool
    that recomputes cost from raw usage would need date-ranged pricing first.
    """

    model_config = ConfigDict(frozen=True)

    model: str
    as_of: date
    prompt_micro_dollars_per_million: int = Field(ge=0)
    completion_micro_dollars_per_million: int = Field(ge=0)
    billable: bool = True

    def model_post_init(self, __context: object) -> None:
        if not self.billable and (
            self.prompt_micro_dollars_per_million != 0
            or self.completion_micro_dollars_per_million != 0
        ):
            raise ValueError(
                f"{self.model}: billable=False but a nonzero rate is set — "
                "a free-tier row must price at exactly zero, not silently discount"
            )


# A MappingProxyType, not a plain dict: PriceEntry is frozen, but a bare module-level dict would
# still let any importer mutate the table itself (`PRICE_TABLE["x"] = ...`) and leak state across
# unrelated callers. The proxy makes that a TypeError instead of a silent global mutation.
#
# Provider swap #2 (2026-09-23): Anthropic -> NVIDIA -> Gemini, per user direction. Model
# confirmed live via client.models.list() against the real API (not docs — the docs-listed
# "gemini-3-flash" 404'd; the real stable id is "gemini-3-flash-preview"). `billable=False` here is
# the user's own statement that their Google AI Studio key is on the free tier, not a fetched
# pricing page — same documented-assumption status the NVIDIA entry had, and just as much in
# need of reconfirming if the key's tier ever changes.
PRICE_TABLE: Mapping[str, PriceEntry] = MappingProxyType(
    {
        "gemini-3-flash-preview": PriceEntry(
            model="gemini-3-flash-preview",
            as_of=date(2026, 9, 23),
            prompt_micro_dollars_per_million=0,
            completion_micro_dollars_per_million=0,
            billable=False,
        ),
        "gemini-3.6-flash": PriceEntry(
            model="gemini-3.6-flash",
            as_of=date(2026, 9, 24),
            prompt_micro_dollars_per_million=0,
            completion_micro_dollars_per_million=0,
            billable=False,
        ),
        "gemini-3.1-flash-lite": PriceEntry(
            model="gemini-3.1-flash-lite",
            as_of=date(2026, 9, 24),
            prompt_micro_dollars_per_million=0,
            completion_micro_dollars_per_million=0,
            billable=False,
        ),
    }
)


def _round_half_up(numerator: int, denominator: int) -> int:
    """Integer division rounding half up. Never negative here: tokens and rates are both >= 0."""
    return (numerator + denominator // 2) // denominator


def price_call(
    model: str, usage: TokenUsage, *, table: Mapping[str, PriceEntry] | None = None
) -> int:
    """Price one model call in micro-dollars, rounding half-up once on the summed cost.

    Raises `UnknownModelError` for a model with no table entry. A known free-tier model
    (`billable=False`) returns 0 without raising — that is an explicit, documented fact about the
    entry, not a fallback.

    Prompt and completion cost are summed as exact numerators over a shared denominator *before*
    rounding, not rounded independently and then added: rounding each component down separately
    can only ever lose fractional micro-dollars, never gain them, which silently biases every
    multi-component price toward undercounting. Rounding the sum once removes that bias.
    """
    # `table if table is not None else PRICE_TABLE`, not `table or PRICE_TABLE`: an explicitly
    # passed empty table (`table={}`) is a real, distinct request — "price against nothing" — and
    # must raise UnknownModelError, not silently fall through to the production table because an
    # empty dict is falsy.
    active_table = table if table is not None else PRICE_TABLE
    entry = active_table.get(model)
    if entry is None:
        raise UnknownModelError(
            f"no price table entry for model {model!r} — add one to PRICE_TABLE "
            "before pricing calls to it (see SPEC-trace-core.md § Cost)"
        )
    # reasoning_tokens (TokenUsage's hidden-thinking-token gap, see records.py) is priced at the
    # completion rate: it is generation-side compute the same way visible completion tokens are,
    # just not itemized separately by the API. Zero for a provider that reports a total that
    # already equals prompt + completion, so this is a no-op everywhere except Gemini 3.
    billable_completion_tokens = usage.completion_tokens + usage.reasoning_tokens
    numerator = (
        usage.prompt_tokens * entry.prompt_micro_dollars_per_million
        + billable_completion_tokens * entry.completion_micro_dollars_per_million
    )
    return _round_half_up(numerator, 1_000_000)
