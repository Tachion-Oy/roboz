"""Filtering and rendering helpers for typed conversation history."""

from collections.abc import Iterable
import json

from roboz.models.core import BaseNames, Message, Role


def render_content(message: Message) -> str:
    """Return model-visible content, prefixing JSON inputs with caller and time.

    Assistant replies and non-object content render verbatim.
    """
    if message.role == Role.ASSISTANT:
        return message.content
    try:
        payload = json.loads(message.content)
    except ValueError:
        return message.content
    if not isinstance(payload, dict):
        return message.content
    envelope = {
        BaseNames.TIMESTAMP_FIELD: message.timestamp.isoformat(timespec="seconds")
    }
    if message.caller is not None:
        envelope = {BaseNames.CALLER_FIELD: message.caller} | envelope
    return json.dumps(envelope | payload, ensure_ascii=False)


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
    Content is decoded only when an action filter is supplied; invalid or
    non-object JSON and missing or null actions do not match that filter.

    Args:
        messages: Conversation history to inspect, in its existing order.
        role: Required conversation role, or None to accept every role.
        action: Exact top-level JSON action identifying a requested tool, or
            None to leave actions unrestricted.
        caller: Exact ``Message.caller`` identifying the producer of a
            recorded result, or None to leave callers unrestricted.

    Returns:
        A new list of matching messages, or an empty list if none match.
        Without filters, all messages are returned.
    """
    matches: list[Message] = []
    for message in messages:
        if role is not None and message.role != role:
            continue
        if caller is not None and message.caller != caller:
            continue
        if action is not None:
            try:
                content = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            match content:
                case {BaseNames.ACTION_FIELD: found} if found == action:
                    pass
                case _:
                    continue
        matches.append(message)
    return matches
