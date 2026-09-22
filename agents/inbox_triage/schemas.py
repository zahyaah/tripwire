"""Tool argument schemas: one pydantic model per tool, generating both the JSON schema sent to
the model and the validator Task 7's agent loop runs a parsed `tool_calls[].function.arguments`
against before calling the implementation (SPEC.md: "Tool inputs are parsed as JSON objects...
never string-matched" — parsing happens at the loop boundary; this module is what it validates
against).

Deriving the JSON schema from the pydantic model (`model_json_schema()`) rather than
hand-duplicating one in prose keeps the two representations from drifting apart — there is
exactly one place that says what `add_label`'s arguments are.

`strict: true` on every schema (Task 6 acceptance criterion) with `additionalProperties: false`
and every property required — following the OpenAI-family strict-tool-use convention of
representing an optional argument as a nullable type rather than an absent key. Whether NVIDIA's
catalog endpoint enforces this as strictly as OpenAI does is unconfirmed (SPEC.md Open Question
6 only confirms tool calling itself is supported, not strict-mode semantics) — Task 7's own
pydantic validation of the parsed arguments is the layer this project actually relies on either
way, so a NIM host that ignores `strict` still gets real enforcement downstream.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class SearchThreadsArgs(BaseModel):
    """Arguments for `search_threads`."""

    model_config = ConfigDict(extra="forbid")

    query: str


class GetThreadArgs(BaseModel):
    """Arguments for `get_thread`."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str


class LookupCustomerArgs(BaseModel):
    """Arguments for `lookup_customer`. Exactly one of `email`/`customer_id` should be given —
    enforced by the tool implementation, not the schema (JSON Schema's "exactly one of" support
    is awkward under `strict: true`'s all-properties-required convention)."""

    model_config = ConfigDict(extra="forbid")

    email: str | None = None
    customer_id: str | None = None


class LookupOrderArgs(BaseModel):
    """Arguments for `lookup_order`."""

    model_config = ConfigDict(extra="forbid")

    order_id: str


class AddLabelArgs(BaseModel):
    """Arguments for `add_label`. `label` is free text, not a fixed enum: labels are the agent's
    own classification output, not a guess at the corpus's hidden ground-truth intent."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    label: str


class DraftReplyArgs(BaseModel):
    """Arguments for `draft_reply`."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    body: str


class EscalateArgs(BaseModel):
    """Arguments for `escalate`."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    reason: str


class SnoozeArgs(BaseModel):
    """Arguments for `snooze`."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    reason: str


class ArchiveArgs(BaseModel):
    """Arguments for `archive`."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    reason: str | None = None


def _tool_schema(name: str, description: str, args_model: type[BaseModel]) -> dict[str, Any]:
    """Build one OpenAI-family tool definition from a pydantic args model."""
    schema = args_model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
        # OpenAI-family strict mode expects nullable-via-anyOf for an optional field, not a
        # "default" key — pydantic emits both; strip the latter to match the convention exactly.
        prop.pop("default", None)
    schema["additionalProperties"] = False
    schema["required"] = list(schema.get("properties", {}).keys())
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema,
            "strict": True,
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool_schema(
        "search_threads",
        "Search the inbox for threads matching a query (subject, body, or sender email). "
        "Returns a short summary of each match — use get_thread for the full transcript.",
        SearchThreadsArgs,
    ),
    _tool_schema(
        "get_thread",
        "Get the full message transcript of one thread by id.",
        GetThreadArgs,
    ),
    _tool_schema(
        "lookup_customer",
        "Look up a customer record by email or customer id. Provide exactly one.",
        LookupCustomerArgs,
    ),
    _tool_schema(
        "lookup_order",
        "Look up an order record by id. Returns an error if the order doesn't exist.",
        LookupOrderArgs,
    ),
    _tool_schema(
        "add_label",
        "Attach a classification label to a thread.",
        AddLabelArgs,
    ),
    _tool_schema(
        "draft_reply",
        "Save a draft reply to a thread. Does not send anything.",
        DraftReplyArgs,
    ),
    _tool_schema(
        "escalate",
        "Escalate a thread to a human, with a reason.",
        EscalateArgs,
    ),
    _tool_schema(
        "snooze",
        "Snooze a thread for later, with a reason.",
        SnoozeArgs,
    ),
    _tool_schema(
        "archive",
        "Archive a thread as resolved or not actionable.",
        ArchiveArgs,
    ),
]
