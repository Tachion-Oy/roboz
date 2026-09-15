"""Filtering helpers for typed conversation history."""

from collections.abc import Iterable
import json

from roboz.models.core import BaseNames, Message, Role


def filter_messages(
    messages: Iterable[Message],
    *,
    role: Role | None = None,
    action: str | None = None,
    caller: str | None = None,
) -> list[Message]:
    """Return history messages matching every supplied filter in order.

    The returned list retains the original objects, including duplicates and
    messages hidden from model context by truncation. No messages are modified.
    Content is decoded only when an action or caller filter is supplied; invalid
    or non-object JSON and missing or null fields do not match those filters.

    Args:
        messages: Conversation history to inspect, in its existing order.
        role: Required conversation role, or None to accept every role.
        action: Exact top-level JSON action identifying a requested tool, or
            None to leave actions unrestricted.
        caller: Exact top-level JSON caller identifying the producer of a
            recorded result, or None to leave callers unrestricted.

    Returns:
        A new list of matching messages, or an empty list if none match.
        Without filters, all messages are returned.
    """
    matches: list[Message] = []
    for message in messages:
        if role is not None and message.role != role:
            continue
        if action is not None or caller is not None:
            try:
                content = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            match content:
                case dict() as payload:
                    if action is not None and payload.get(BaseNames.ACTION_FIELD) != action:
                        continue
                    if caller is not None and payload.get(BaseNames.CALLER_FIELD) != caller:
                        continue
                case _:
                    continue
        matches.append(message)
    return matches
