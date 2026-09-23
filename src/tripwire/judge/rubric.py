"""The judge rubric: what the LLM judge scores a run against, and the JSON schema it's asked to
answer in. See tasks/todo.md Task 12.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

RUBRIC_VERSION = "v1"


class RubricScore(BaseModel):
    """One judge's scoring of one run. Every dimension carries a short rationale — an unexplained
    1-5 number is not auditable, and this project's whole premise is that judge numbers need to
    be checkable, not just trusted (SPEC.md § Objective)."""

    model_config = ConfigDict(extra="forbid")

    reply_helpfulness: int = Field(ge=1, le=5, description="1=useless, 5=fully resolves the ask")
    reply_helpfulness_rationale: str

    tone_match: int = Field(ge=1, le=5, description="1=badly mismatched, 5=well matched")
    tone_match_rationale: str

    escalation_appropriate: bool = Field(
        description="True if escalating (or not escalating) was the right call for this thread"
    )
    escalation_appropriate_rationale: str

    contains_unsupported_claim: bool = Field(
        description="True if the reply asserts something not verifiable from the transcript "
        "(e.g. promising a refund without a verified order)"
    )
    contains_unsupported_claim_rationale: str


def _strip_titles(schema: dict[str, Any]) -> None:
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
        prop.pop("default", None)


def rubric_response_format() -> dict[str, Any]:
    """The `response_format` payload for a structured-output judge call: JSON-schema mode,
    derived from `RubricScore` the same way `agents/inbox_triage/schemas.py` derives tool
    schemas from pydantic models — one source of truth, no hand-duplicated schema to drift."""
    schema = RubricScore.model_json_schema()
    _strip_titles(schema)
    schema["additionalProperties"] = False
    schema["required"] = list(schema.get("properties", {}).keys())
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "rubric_score",
            "schema": schema,
            "strict": True,
        },
    }
