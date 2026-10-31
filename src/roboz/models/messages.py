"""Filtering and rendering helpers for typed conversation history."""

from collections.abc import Iterable
import json

from roboz.models.core import BOOTSTRAP_MESSAGE_KINDS, BaseNames, Message, Role


def _shows_timestamp(message: Message) -> bool:
    # Assistant replies stay verbatim so the model does not learn to emit
    # timestamps itself. System and bootstrap messages stay byte-identical
    # across runs so providers can reuse their cached prompt prefix.
    return (
        message.role not in {Role.ASSISTANT, Role.SYSTEM}
        and message.message_kind not in BOOTSTRAP_MESSAGE_KINDS
    )


def format_timestamp(message: Message) -> str:
    """Return the message time as local ISO 8601 with offset, in seconds."""
    return message.timestamp.astimezone().isoformat(timespec="seconds")


def render_content(message: Message, *, include_timestamp: bool = True) -> str:
    """Return model-visible content with the message's caller and time.

    JSON object content, the format of agent tool results, receives leading
    ``caller`` and ``timestamp`` fields; payload fields with the same names take
    precedence. Other content is returned verbatim, so plain prompts sent
    directly to a model are unchanged. Assistant, system, and bootstrap
    messages never show a timestamp.

    Args:
        message: Message to render; it is not modified.
        include_timestamp: Whether eligible messages show their timestamp.
    """
    envelope: dict[str, str] = {}
    if message.caller is not None:
        envelope[BaseNames.CALLER_FIELD] = message.caller
    if include_timestamp and _shows_timestamp(message):
        envelope[BaseNames.TIMESTAMP_FIELD] = format_timestamp(message)
    if not envelope:
        return message.content
    try:
        payload = json.loads(message.content)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        return message.content
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
