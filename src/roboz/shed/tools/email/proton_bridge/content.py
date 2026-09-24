"""Interpret bounded MIME content, addresses, threading, and attachments."""

import re
from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser

from ..contracts import (
    DownloadedEmailAttachment,
    EmailMessageAttachment,
    EmailProviderError,
)
from .references import encode_attachment_reference

MAX_QUOTED_BODY_CHARS = 100_000

_MESSAGE_ID_PATTERN = re.compile(r"<[^<>\s]+@[^<>\s]+>")


def display_header(
    message: Message,
    name: str,
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
    reply_to = header_addresses(message, "Reply-To")
    return reply_to or header_addresses(message, "From")


def header_addresses(message: Message, name: str) -> tuple[str, ...]:
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
    original_to = header_addresses(message, "To") if include_original_recipients else ()
    original_cc = header_addresses(message, "Cc") if include_original_recipients else ()
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
    references = _message_ids(message.get("References"))
    if not references:
        in_reply_to = _message_ids(message.get("In-Reply-To"))
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


_BLOCK_TAGS = frozenset(
    "address article aside blockquote br div footer h1 h2 h3 h4 h5 h6 header hr li main p pre section table tr".split()
)
_IGNORED_TAGS = frozenset({"head", "script", "style"})


@dataclass(frozen=True)
class ExtractedMessageBody:
    """Readable source text plus whether any input had to be truncated."""

    text: str | None
    truncated: bool


def extract_message_body(
    message: Message,
    *,
    source_truncated: bool,
    max_chars: int = MAX_QUOTED_BODY_CHARS,
) -> ExtractedMessageBody:
    """Prefer plain text, with a conservative HTML-to-text fallback."""
    plain = _first_text_part(message, "text/plain")
    text = plain
    if text is None:
        html = _first_text_part(message, "text/html")
        text = _html_to_text(html) if html is not None else None
    if text is None:
        return ExtractedMessageBody(text=None, truncated=source_truncated)

    normalized = _normalize(text)
    if not normalized:
        return ExtractedMessageBody(text=None, truncated=source_truncated)
    body_truncated = len(normalized) > max_chars
    if body_truncated:
        normalized = normalized[:max_chars].rstrip()
    return ExtractedMessageBody(
        text=normalized,
        truncated=source_truncated or body_truncated,
    )


def _first_text_part(message: Message, content_type: str) -> str | None:
    for part in message.walk():
        if (
            part.is_multipart()
            or part.get_content_disposition() == "attachment"
            or part.get_content_type() != content_type
        ):
            continue
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = part.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace")
            except LookupError:
                return payload.decode("utf-8", errors="replace")
        raw_payload = part.get_payload()
        if isinstance(raw_payload, str):
            return raw_payload
    return None


def _normalize(text: str) -> str:
    return (
        text.replace("\x00", "\N{REPLACEMENT CHARACTER}")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        """Collect visible text, ignoring active and document-head content."""
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track HTML blocks and readable line breaks."""
        del attrs
        if tag in _IGNORED_TAGS:
            self.ignored_depth += 1
        elif self.ignored_depth == 0 and tag in _BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Close tracked HTML blocks and add readable spacing."""
        if tag in _IGNORED_TAGS and self.ignored_depth > 0:
            self.ignored_depth -= 1
        elif self.ignored_depth == 0 and tag in _BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_data(self, data: str) -> None:
        """Collect visible HTML text from the bounded source message."""
        if self.ignored_depth == 0:
            self.fragments.append(data)


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    lines = (" ".join(line.split()) for line in "".join(parser.fragments).splitlines())
    return "\n".join(line for line in lines if line)


def attachment_metadata(
    message: Message, source_message_ref: str
) -> tuple[EmailMessageAttachment, ...]:
    """Describe downloadable attachments with opaque source references."""
    return tuple(
        EmailMessageAttachment(
            attachment_ref=encode_attachment_reference(source_message_ref, index),
            filename=_display_filename(part.get_filename()),
            content_type=part.get_content_type()[:255],
            size=len(_payload(part)),
        )
        for index, part in enumerate(_attachment_parts(message))
    )


def downloaded_attachment(message: Message, index: int) -> DownloadedEmailAttachment:
    """Return the selected attachment bytes and safe display metadata."""
    parts = _attachment_parts(message)
    if index >= len(parts):
        raise EmailProviderError("email attachment no longer exists")
    part = parts[index]
    return DownloadedEmailAttachment(
        filename=_display_filename(part.get_filename()),
        content_type=part.get_content_type()[:255],
        data=_payload(part),
    )


def _attachment_parts(message: Message) -> list[Message]:
    return [
        part
        for part in message.walk()
        if not part.is_multipart()
        and (
            part.get_content_disposition() == "attachment"
            or part.get_filename() is not None
        )
    ]


def _payload(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    return payload if isinstance(payload, bytes) else b""


def _display_filename(value: str | None) -> str:
    if value is None:
        return "(unnamed attachment)"
    normalized = " ".join(value.replace("\x00", "\N{REPLACEMENT CHARACTER}").split())
    return normalized[:255] or "(unnamed attachment)"
