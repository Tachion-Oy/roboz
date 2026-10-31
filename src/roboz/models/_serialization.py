"""Serialization helpers for values containing concrete nested models."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from roboz.models._fields import runtime_fields
from roboz.models._telemetry import LLMTelemetry
from roboz.models.core import RESERVED_MESSAGE_FIELDS, Empty, Invoke, Message, MessageKind, Role, Stop

if TYPE_CHECKING:
    from roboz.tooling.core import Tool


def camel_to_snake(s: str) -> str:
    return "".join(["_" + c.lower() if c.isupper() else c for c in s]).lstrip("_")


def reduce_escapes(text: str) -> str:
    def _collapse(match: re.Match[str]) -> str:
        following = match.group(2)
        return "\\" + following if following is not None else match.group(1)

    return re.sub(r'(\\+)(["\'n])?', _collapse, text)


def get_finalized_message(
    output: Invoke | Empty | Stop,
    caller: Tool | None = None,
    *,
    message_kind: MessageKind | None = None,
) -> Message:
    """Record invocation arguments as JSON and attach images from tool results.

    Tool results record the producing tool in ``Message.caller`` rather than in
    their JSON content, so their payloads must not use ``caller`` or
    ``timestamp`` fields.

    Raises:
        ValueError: If a tool result payload contains a reserved field.

    Invocation payloads retain supplied fields without inserting unset defaults.
    Result images become message attachments instead of duplicated JSON text.
    """
    excluded_fields = runtime_fields(type(output))
    metadata = {
        "truncation": output.truncation,
        "message_kind": (
            message_kind if message_kind is not None else output.message_kind
        ),
        **output.model_dump(include=set(LLMTelemetry.model_fields), mode="python"),
    }
    if isinstance(output, Invoke):
        payload = output.model_dump(
            mode="json", exclude=excluded_fields, exclude_unset=True
        )
        return Message(
            role=Role.ASSISTANT,
            content=json.dumps(payload, ensure_ascii=False),
            **metadata,
        )

    payload = output.model_dump(mode="json", exclude=excluded_fields | {"images"})
    if reserved := RESERVED_MESSAGE_FIELDS & payload.keys():
        raise ValueError(f"Tool output uses reserved fields: {sorted(reserved)}")
    images = []
    try:
        images = getattr(output, "images")
    except AttributeError:
        pass
    return Message(
        role=Role.USER,
        content=json.dumps(payload, ensure_ascii=False),
        images=images,
        caller=caller.name if caller is not None else None,
        **metadata,
    )
