"""Parse and interpret the small RFC header subset used by reply drafts."""

from __future__ import annotations

import re
from email import policy
from email.message import Message
from email.parser import BytesParser

from ..contracts import EmailProviderError
from .protocol import MAX_FETCHED_HEADER_BYTES, EmailHeader

_MESSAGE_ID_PATTERN = re.compile(r"<[^<>\s]+@[^<>\s]+>")


def parse_headers(raw_headers: bytes) -> Message:
    """Parse the bounded headers returned by the IMAP server."""
    if len(raw_headers) > MAX_FETCHED_HEADER_BYTES:
        raise EmailProviderError("email source headers are too large")
    return BytesParser(policy=policy.default).parsebytes(raw_headers, headersonly=True)


def display_header(
    message: Message,
    name: EmailHeader,
    *,
    default: str = "(unknown)",
    max_chars: int = 998,
) -> str:
    """Return a safe, length-limited header for display."""
    value = message.get(name)
    if value is None:
        return default
    normalized = " ".join(str(value).split())
    return normalized[:max_chars] if normalized else default


def reply_addresses(message: Message) -> tuple[str, ...]:
    """Return the explicit reply target, preferring Reply-To over From."""
    reply_to = header_addresses(message, EmailHeader.REPLY_TO)
    return reply_to or header_addresses(message, EmailHeader.FROM)


def header_addresses(message: Message, name: EmailHeader) -> tuple[str, ...]:
    """Return the unique valid addresses in a message header."""
    addresses: list[str] = []
    for header in message.get_all(name, []):
        for address in getattr(header, "addresses", ()):
            addr_spec = getattr(address, "addr_spec", "")
            if addr_spec and "@" in addr_spec:
                addresses.append(addr_spec)
    return _unique_addresses(tuple(addresses))

def reply_all_addresses(
    message: Message,
    *,
    own_addresses: tuple[str, ...],
    include_original_recipients: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Derive safe To/Cc fields with stable ordering and no self-addresses."""
    reply_targets = reply_addresses(message)
    original_to = (
        header_addresses(message, EmailHeader.TO)
        if include_original_recipients
        else ()
    )
    original_cc = (
        header_addresses(message, EmailHeader.CC)
        if include_original_recipients
        else ()
    )
    excluded = {address.casefold() for address in own_addresses}
    to = _unique_addresses((*reply_targets, *original_to), excluded=excluded)
    cc = _unique_addresses(
        original_cc,
        excluded=excluded | {address.casefold() for address in to},
    )
    return to, cc


def single_message_id(value: object) -> str | None:
    """Accept one syntactically valid Message-ID value."""
    if value is None:
        return None
    matches = _MESSAGE_ID_PATTERN.findall(str(value))
    return matches[0] if len(matches) == 1 else None


def reply_references(message: Message, parent_message_id: str) -> tuple[str, ...]:
    """Build bounded thread references from source headers."""
    references = _message_ids(message.get(EmailHeader.REFERENCES))
    if not references:
        in_reply_to = _message_ids(message.get(EmailHeader.IN_REPLY_TO))
        if len(in_reply_to) == 1:
            references = in_reply_to
    if not references or references[-1] != parent_message_id:
        references = (*references, parent_message_id)
    return references


def _message_ids(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(_MESSAGE_ID_PATTERN.findall(str(value)))


def _unique_addresses(
    addresses: tuple[str, ...],
    *,
    excluded: set[str] | None = None,
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen = set(excluded or ())
    for address in addresses:
        key = address.casefold()
        if key not in seen:
            normalized.append(address)
            seen.add(key)
    return tuple(normalized)
