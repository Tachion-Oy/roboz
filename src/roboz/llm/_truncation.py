"""Conversation truncation helpers for bounded model requests."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Final

from roboz.models import BaseNames, Role, render_content
from roboz.models.truncation import (
    LIGHT_MAX_CHARS,
    Severity,
    Truncation,
    TruncationSpec,
)

if TYPE_CHECKING:
    from roboz.models import Message


def select_truncation(
    distance: int,
    rules: TruncationSpec,
) -> Truncation | None:
    """Among rules where distance >= threshold and threshold >= 0, return the one with max threshold."""
    normalized = Truncation.to_rules_list(rules)
    applicable = [r for r in normalized if r.threshold >= 0 and distance >= r.threshold]
    return (
        max(applicable, key=lambda r: (r.threshold, r.severity)) if applicable else None
    )


TRUNCATED_PLACEHOLDER: Final[str] = "<message truncated>"


def _truncate_str_values(obj: object, max_len: int) -> object:
    """Recursively truncate string values in JSON-like structures."""
    if isinstance(obj, str):
        return obj[:max_len] + "..." if len(obj) > max_len else obj
    if isinstance(obj, dict):
        return {k: _truncate_str_values(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_str_values(v, max_len) for v in obj]
    return obj


def truncate_content_for_context(content: str, severity: Severity) -> str | None:
    """Truncate message content for agent context based on severity.

    STUB: Keep only action + placeholder.
    LIGHT: Truncate string fields to
    LIGHT_MAX_CHARS chars.
    """
    match severity:
        case Severity.REMOVE:
            return None
        case Severity.STUB:
            try:
                data = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return TRUNCATED_PLACEHOLDER
            if not isinstance(data, dict):
                return TRUNCATED_PLACEHOLDER
            stub: dict[str, str] = {"truncated": TRUNCATED_PLACEHOLDER}
            if BaseNames.ACTION_FIELD in data:
                stub[BaseNames.ACTION_FIELD] = str(data[BaseNames.ACTION_FIELD])
            return json.dumps(stub)

        case Severity.LIGHT:
            try:
                data = json.loads(content)
                truncated = _truncate_str_values(data, LIGHT_MAX_CHARS)
                return json.dumps(truncated)
            except (json.JSONDecodeError, TypeError):
                return (
                    content[:LIGHT_MAX_CHARS] + "..."
                    if len(content) > LIGHT_MAX_CHARS
                    else content
                )
    return content


def _truncate_message_for_context(
    message: Message,
    severity: Severity,
    truncate_content: Callable[[str, Severity], str | None],
) -> Message | None:
    """Apply truncation to one message's text and attachments together."""
    content = truncate_content(message.content, severity)
    if content is None:
        return None
    update: dict[str, object] = {"content": content}
    if severity == Severity.STUB:
        update["images"] = []
    return message.model_copy(update=update)


def get_truncated_messages_for_context(
    messages: list[Message],
    select_rule: Callable[[int, TruncationSpec], Truncation | None] = select_truncation,
    truncate_content: Callable[
        [str, Severity], str | None
    ] = truncate_content_for_context,
) -> list[Message]:
    """Select truncation rules by message age and collect the surviving messages.

    Injecting callables allows decoupled testing and custom strategies while preserving
    prior behavior through defaults.
    """
    result: list[Message] = []
    n = len(messages)
    for i, message in enumerate(messages):
        if message.role == Role.SYSTEM:
            result.append(message)
            continue
        distance = n - 1 - i
        selected = select_rule(distance, message.truncation)
        if selected is None:
            result.append(message)
            continue
        truncated = _truncate_message_for_context(
            message, selected.severity, truncate_content
        )
        if truncated is not None:
            result.append(truncated)
    return result


_ESTIMATED_CHARS_PER_TOKEN = 4


def estimate_conversation_tokens(messages: list[Message]) -> int:
    """Estimate text tokens after context truncation; image tokens are not counted."""
    return sum(
        math.ceil(len(render_content(message)) / _ESTIMATED_CHARS_PER_TOKEN)
        for message in get_truncated_messages_for_context(messages)
    )
