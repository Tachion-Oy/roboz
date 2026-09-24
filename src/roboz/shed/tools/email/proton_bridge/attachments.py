"""MIME attachment metadata and payload extraction."""

from email import policy
from email.message import Message
from email.parser import BytesParser

from ..contracts import (
    DownloadedEmailAttachment,
    EmailMessageAttachment,
    EmailProviderError,
)
from .imap_codec import encode_attachment_reference


def parse_message(raw_message: bytes) -> Message:
    """Parse a complete RFC email message without executing its content."""
    return BytesParser(policy=policy.default).parsebytes(raw_message)


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
