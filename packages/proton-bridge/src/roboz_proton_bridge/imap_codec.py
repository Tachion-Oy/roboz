"""Small codecs for IMAP arguments, responses, identifiers, and references."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime

from roboz_shed.tools.email.contracts import EmailProviderError, EmailSearchRequest
from .protocol import (
    IMAP_ATTACHMENT_REFERENCE_PREFIX,
    IMAP_SOURCE_REFERENCE_PREFIX,
    IMAP_UID_IDENTIFIER_PREFIX,
    ImapResponseCode,
    ImapResponseStatus,
    ImapSearchKey,
)

_MAILBOX_NAME_PATTERN = re.compile(r'(?:"([^"]+)"|([^ ]+))\s*$')
_APPEND_UID_PATTERN = re.compile(
    rf"{ImapResponseCode.APPEND_UID}\s+\d+\s+(\d+)".encode("ascii")
)
_UIDVALIDITY_PATTERN = re.compile(rb"\b(\d+)\b")
_UID_PATTERN = re.compile(rb"\bUID\s+([1-9]\d*)\b")
_INTERNALDATE_PATTERN = re.compile(
    rb'INTERNALDATE\s+"(\d{1,2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2} [+-]\d{4})"'
)
_FLAGS_PATTERN = re.compile(rb"\bFLAGS\s+\(([^)]*)\)", re.IGNORECASE)


@dataclass(frozen=True)
class ImapSourceReference:
    """Decoded stable reference to one IMAP message UID."""

    mailbox: str
    uid_validity: int
    uid: bytes


@dataclass(frozen=True)
class ImapAttachmentReference:
    """Decoded stable reference to an attachment within one message."""

    source_message_ref: str
    attachment_index: int


def require_ok(status: str, failure_message: str) -> None:
    """Require an IMAP OK response or raise a safe provider error."""
    if status.upper() != ImapResponseStatus.OK:
        raise EmailProviderError(failure_message)


def uid_identifier(uid: bytes) -> str:
    """Encode an IMAP UID as a displayable identifier."""
    try:
        value = uid.decode("ascii")
    except UnicodeDecodeError as exc:
        raise EmailProviderError(
            "Email provider returned an invalid message UID"
        ) from exc
    return f"{IMAP_UID_IDENTIFIER_PREFIX}{value}"


def mailbox_name(list_response: str) -> str:
    """Parse the terminal mailbox name from one IMAP LIST response."""
    match = _MAILBOX_NAME_PATTERN.search(list_response)
    if match is None:
        raise EmailProviderError("Could not parse the Proton Mail Bridge mailbox")
    return match.group(1) or match.group(2)


def append_uid(response: list[bytes]) -> str | None:
    """Extract the appended message UID from an IMAP response when present."""
    for part in response:
        match = _APPEND_UID_PATTERN.search(part)
        if match is not None:
            return uid_identifier(match.group(1))
    return None


def search_criteria(request: EmailSearchRequest) -> tuple[str | bytes, ...]:
    """Encode a normalized email request as IMAP search criteria."""
    criteria: list[str | bytes] = []
    if request.from_address:
        criteria.extend((ImapSearchKey.FROM, imap_quote(request.from_address)))
    if request.to_address:
        criteria.extend((ImapSearchKey.TO, imap_quote(request.to_address)))
    if request.subject_contains:
        criteria.extend((ImapSearchKey.SUBJECT, imap_quote(request.subject_contains)))
    if request.text_contains:
        criteria.extend((ImapSearchKey.TEXT, imap_quote(request.text_contains)))
    if request.since:
        criteria.extend((ImapSearchKey.SINCE, request.since.strftime("%d-%b-%Y")))
    if request.before:
        criteria.extend((ImapSearchKey.BEFORE, request.before.strftime("%d-%b-%Y")))
    return tuple(criteria or (ImapSearchKey.ALL,))


def imap_quote(value: str) -> bytes:
    """Quote a safe text value for an IMAP command argument."""
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise EmailProviderError("email search contains invalid control characters")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'.encode()


def uid_validity(response: object) -> int:
    """Parse a mailbox UIDVALIDITY value from an IMAP response."""
    value = response_value(response)
    match = _UIDVALIDITY_PATTERN.search(value)
    if match is None:
        raise EmailProviderError(
            "Email provider returned an invalid mailbox UIDVALIDITY"
        )
    return int(match.group(1))


def response_value(response: object) -> bytes:
    """Flatten the data portion of a conventional IMAP response."""
    if not isinstance(response, tuple) or len(response) != 2:
        return b""
    data = response[1]
    if isinstance(data, (list, tuple)):
        return b" ".join(item for item in data if isinstance(item, bytes))
    return data if isinstance(data, bytes) else b""


def fetch_response_parts(response: object) -> tuple[bytes, bytes | None]:
    """Separate metadata and payload bytes from an IMAP FETCH response."""
    if not isinstance(response, (list, tuple)):
        return b"", None
    metadata = b""
    raw_headers: bytes | None = None
    for item in response:
        if isinstance(item, tuple) and len(item) >= 2:
            if isinstance(item[0], bytes):
                metadata += b" " + item[0]
            if isinstance(item[1], bytes):
                raw_headers = item[1]
        elif isinstance(item, bytes):
            metadata += b" " + item
    return metadata, raw_headers


def internal_date(metadata: bytes) -> datetime | None:
    """Parse INTERNALDATE metadata when it is valid and present."""
    match = _INTERNALDATE_PATTERN.search(metadata)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1).decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
    except (UnicodeDecodeError, ValueError):
        return None


def uid_message_metadata(
    response: object,
) -> tuple[tuple[bytes, datetime, bool], ...]:
    """Parse UID, INTERNALDATE, and account-level Seen state."""
    if not isinstance(response, (list, tuple)):
        return ()
    parsed: list[tuple[bytes, datetime, bool]] = []
    seen_uids: set[bytes] = set()
    for item in response:
        metadata = item[0] if isinstance(item, tuple) and item else item
        if not isinstance(metadata, bytes):
            continue
        uid_match = _UID_PATTERN.search(metadata)
        flags_match = _FLAGS_PATTERN.search(metadata)
        received_at = internal_date(metadata)
        if uid_match is None or flags_match is None or received_at is None:
            continue
        uid = uid_match.group(1)
        flags = flags_match.group(1).lower().split()
        if uid not in seen_uids:
            parsed.append((uid, received_at, rb"\seen".lower() in flags))
            seen_uids.add(uid)
    return tuple(parsed)


def encode_source_reference(mailbox: str, uid_validity: int, uid: bytes) -> str:
    """Encode mailbox identity and UID state as an opaque source reference."""
    try:
        uid_text = uid.decode("ascii")
    except UnicodeDecodeError as exc:
        raise EmailProviderError(
            "Email provider returned an invalid message UID"
        ) from exc
    payload = f"{mailbox}\x00{uid_validity}\x00{uid_text}".encode()
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{IMAP_SOURCE_REFERENCE_PREFIX}{encoded}"


def decode_source_reference(value: str) -> ImapSourceReference:
    """Decode and validate an opaque IMAP source reference."""
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
        uid = raw_uid.encode("ascii")
    except (UnicodeError, ValueError) as exc:
        raise EmailProviderError("email source reference is invalid") from exc
    if not mailbox or validity <= 0 or re.fullmatch(rb"[1-9]\d*", uid) is None:
        raise EmailProviderError("email source reference is invalid")
    return ImapSourceReference(mailbox=mailbox, uid_validity=validity, uid=uid)


def encode_attachment_reference(source_message_ref: str, attachment_index: int) -> str:
    """Encode a source reference and attachment index as an opaque reference."""
    if attachment_index < 0:
        raise ValueError("attachment_index must not be negative")
    payload = f"{source_message_ref}\x00{attachment_index}".encode()
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{IMAP_ATTACHMENT_REFERENCE_PREFIX}{encoded}"


def decode_attachment_reference(value: str) -> ImapAttachmentReference:
    """Decode and validate an opaque IMAP attachment reference."""
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
