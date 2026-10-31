"""Filtering and rendering helpers for typed conversation history."""

from collections.abc import Iterable
import json

from roboz.models.core import BOOTSTRAP_MESSAGE_KINDS, BaseNames, Message, Role


def format_timestamp(message: Message) -> str:
    """Return the message time as ISO 8601 with its creation offset, in seconds."""
    return message.timestamp.isoformat(timespec="seconds")


def render_content(message: Message, *, include_timestamp: bool = True) -> str:
    """Return model-visible content, adding caller and time to tool results.

    Only messages with a ``caller`` are tool results. When their content is a
    JSON object, it receives leading ``caller`` and ``timestamp`` fields;
    bootstrap results omit the timestamp so they stay identical across runs and
    providers can reuse their cached prompt. All other content, including JSON
    sent directly to a model, is returned verbatim.

    Args:
        message: Message to render; it is not modified.
        include_timestamp: Whether non-bootstrap tool results show their time.
    """
    if message.caller is None:
        return message.content
    try:
        payload = json.loads(message.content)
    except json.JSONDecodeError:
        return message.content
    if not isinstance(payload, dict):
        return message.content
    envelope = {BaseNames.CALLER_FIELD: message.caller}
    if include_timestamp and message.message_kind not in BOOTSTRAP_MESSAGE_KINDS:
        envelope[BaseNames.TIMESTAMP_FIELD] = format_timestamp(message)
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
