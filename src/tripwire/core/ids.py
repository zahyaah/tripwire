"""Sortable id generation for runs and spans.

Run ids double as the correlation id threaded through every span in a trace (see
SPEC-trace-core.md § Records). They are lexicographically sortable by creation time, so
`ls runs/` and a plain string sort both give chronological order without parsing anything.
"""

from __future__ import annotations

import random
import string
from datetime import UTC, datetime

_ALPHABET = string.ascii_lowercase + string.digits


def _random_suffix(length: int) -> str:
    return "".join(random.choices(_ALPHABET, k=length))


def new_run_id(now: datetime | None = None) -> str:
    """A sortable run id: UTC timestamp to the microsecond, plus a random suffix to break ties."""
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%f")
    return f"run_{timestamp}-{_random_suffix(6)}"


def new_span_id() -> str:
    """An opaque span id. Not sortable by design — only step_index carries ordering meaning."""
    return f"sp_{_random_suffix(16)}"
