"""The instrumented model gateway and cassette record/replay. See SPEC-trace-core.md."""

from __future__ import annotations

from tripwire.llm.cassettes import (
    CassetteRecord,
    CassetteStore,
    CorruptCassetteError,
    compute_cassette_key,
)
from tripwire.llm.gateway import (
    STREAMING_MAX_TOKENS_THRESHOLD,
    CassetteMissError,
    ModelGateway,
    ModelRequest,
    usage_from_completion,
)

__all__ = [
    "STREAMING_MAX_TOKENS_THRESHOLD",
    "CassetteMissError",
    "CassetteRecord",
    "CassetteStore",
    "CorruptCassetteError",
    "ModelGateway",
    "ModelRequest",
    "compute_cassette_key",
    "usage_from_completion",
]
