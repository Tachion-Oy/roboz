"""Schema transformation helpers for model-facing tool definitions."""

from __future__ import annotations

import types
from typing import Union, get_args, get_origin

from roboz.models._fields import runtime_fields
from roboz.models.core import Message
from roboz.models.messages import render_content


def get_constituent_types(type_: type) -> tuple[type, ...]:
    """Return constituent types for validation: union members or a single type as tuple."""
    origin = get_origin(type_)
    if origin in (types.UnionType, Union):
        return get_args(type_)
    return (type_,)


def messages_scrubber(messages: list[Message]) -> list[dict]:
    """Serialize messages for provider requests with caller and time rendered.

    Runtime metadata is excluded; ``caller`` and ``timestamp`` reach the model
    only through the rendered content.
    """
    allowed = set(Message.model_fields) - runtime_fields(Message)
    return [
        message.model_dump(mode="json", include=allowed, exclude_none=True)
        | {"content": render_content(message)}
        for message in messages
    ]
