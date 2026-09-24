"""Opaque mailbox and attachment references, validated before use."""

import base64
import re
from dataclasses import dataclass

from ..contracts import EmailProviderError

IMAP_SOURCE_REFERENCE_PREFIX = "proton-imap-message:v1:"
IMAP_ATTACHMENT_REFERENCE_PREFIX = "proton-imap-attachment:v1:"


@dataclass(frozen=True)
class ImapSourceReference:
    """Mailbox identity and UID for one searched message."""

    mailbox: str
    uid_validity: int
    uid: int


@dataclass(frozen=True)
class ImapAttachmentReference:
    """Source message and part index for one attachment."""

    source_message_ref: str
    attachment_index: int


def encode_source_reference(mailbox: str, uid_validity: int, uid: int) -> str:
    """Encode a mailbox and UID into an opaque source reference."""
    payload = f"{mailbox}\x00{uid_validity}\x00{uid}".encode()
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{IMAP_SOURCE_REFERENCE_PREFIX}{encoded}"


def decode_source_reference(value: str) -> ImapSourceReference:
    """Validate and decode an opaque source reference."""
    if not value.startswith(IMAP_SOURCE_REFERENCE_PREFIX):
        raise EmailProviderError("email source reference is invalid")
    encoded = value.removeprefix(IMAP_SOURCE_REFERENCE_PREFIX)
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        ).decode("utf-8")
        mailbox, raw_uid_validity, raw_uid = decoded.split("\x00")
        validity = int(raw_uid_validity)
        uid = int(raw_uid)
    except (UnicodeError, ValueError) as exc:
        raise EmailProviderError("email source reference is invalid") from exc
    if (
        not mailbox
        or any(c in mailbox for c in "\r\n")
        or validity <= 0
        or re.fullmatch(r"[1-9][0-9]*", raw_uid) is None
    ):
        raise EmailProviderError("email source reference is invalid")
    return ImapSourceReference(mailbox=mailbox, uid_validity=validity, uid=uid)


def encode_attachment_reference(source_message_ref: str, attachment_index: int) -> str:
    """Encode a source and part index into an attachment reference."""
    if attachment_index < 0:
        raise ValueError("attachment_index must not be negative")
    payload = f"{source_message_ref}\x00{attachment_index}".encode()
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{IMAP_ATTACHMENT_REFERENCE_PREFIX}{encoded}"


def decode_attachment_reference(value: str) -> ImapAttachmentReference:
    """Validate and decode an attachment reference."""
    if not value.startswith(IMAP_ATTACHMENT_REFERENCE_PREFIX):
        raise EmailProviderError("email attachment reference is invalid")
    encoded = value.removeprefix(IMAP_ATTACHMENT_REFERENCE_PREFIX)
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        ).decode("utf-8")
        source_message_ref, raw_index = decoded.rsplit("\x00", 1)
        attachment_index = int(raw_index)
    except (UnicodeError, ValueError) as exc:
        raise EmailProviderError("email attachment reference is invalid") from exc
    decode_source_reference(source_message_ref)
    if attachment_index < 0:
        raise EmailProviderError("email attachment reference is invalid")
    return ImapAttachmentReference(source_message_ref, attachment_index)
