"""Bounded extraction of readable text from an RFC email message."""

from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from html.parser import HTMLParser

from .protocol import MAX_QUOTED_BODY_CHARS

_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "p",
        "pre",
        "section",
        "table",
        "tr",
    }
)
_IGNORED_TAGS = frozenset({"head", "script", "style"})


@dataclass(frozen=True)
class ExtractedMessageBody:
    """Readable source text plus whether any input had to be truncated."""

    text: str | None
    truncated: bool


def extract_message_body(
    raw_message: bytes,
    *,
    source_truncated: bool,
    max_chars: int = MAX_QUOTED_BODY_CHARS,
) -> ExtractedMessageBody:
    """Prefer plain text, with a conservative HTML-to-text fallback."""
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
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
        """Store caller-provided settings and the injectable IMAP transport."""
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
        """Collect visible HTML text within the configured bound."""
        if self.ignored_depth == 0:
            self.fragments.append(data)


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    lines = (" ".join(line.split()) for line in "".join(parser.fragments).splitlines())
    return "\n".join(line for line in lines if line)
