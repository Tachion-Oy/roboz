"""Serialization helpers for values containing concrete nested models."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel

from roboz.models._schema import extract_fields_and_type_names
from roboz.models._telemetry import LLMTelemetry
from roboz.models.core import BaseNames, Empty, Invoke, Message, MessageKind, Role, Stop

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
    dump = output.model_dump(
        mode="json", exclude=set(extract_fields_and_type_names()[0])
    )
    if get_role(output) == Role.USER:
        injected_payload = {}
        if caller is not None:
            injected_payload[BaseNames.CALLER_FIELD] = caller.name
        dump = injected_payload | dump
    resolved_message_kind = (
        message_kind if message_kind is not None else output.message_kind
    )
    return Message(
        role=get_role(output),
        content=json.dumps(dump, ensure_ascii=False),
        truncation=output.truncation,
        message_kind=resolved_message_kind,
        **output.model_dump(
            include=set(LLMTelemetry.model_fields.keys()), mode="python"
        ),
    )


def get_role(output: BaseModel):
    return Role.ASSISTANT if issubclass(type(output), Invoke) else Role.USER
