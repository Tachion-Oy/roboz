"""Mailbox discovery, selection, and bounded message fetching."""

from __future__ import annotations

import imaplib
from datetime import datetime
from email.message import Message
from typing import Any

from roboshed.tools.email.contracts import EmailMailbox, EmailProviderError
from .imap_codec import (
    ImapSourceReference,
    decode_source_reference,
    fetch_response_parts,
    internal_date,
    mailbox_name,
    require_ok,
    uid_validity,
)
from .message_headers import parse_headers
from .protocol import (
    IMAP_INBOX_MAILBOX,
    MAX_FULL_MESSAGE_BYTES,
    EmailHeader,
    ImapMailboxAttribute,
    ImapUidCommand,
)

_FETCH_HEADERS = (
    f"{EmailHeader.DATE} {EmailHeader.FROM} {EmailHeader.REPLY_TO} "
    f"{EmailHeader.TO} {EmailHeader.CC} {EmailHeader.BCC} "
    f"{EmailHeader.SUBJECT} {EmailHeader.MESSAGE_ID} "
    f"{EmailHeader.IN_REPLY_TO} {EmailHeader.REFERENCES}"
)


def mailbox_for(connection: Any, mailbox: EmailMailbox) -> str:
    """Resolve a logical mailbox to its unique physical IMAP name."""
    if mailbox is EmailMailbox.INBOX:
        return IMAP_INBOX_MAILBOX
    attribute = (
        ImapMailboxAttribute.DRAFTS
        if mailbox is EmailMailbox.DRAFTS
        else ImapMailboxAttribute.SENT
    )
    status, response = connection.list()
    require_ok(status, "Could not enumerate email mailboxes")
    matches = [
        mailbox_name(line.decode("utf-8", errors="replace"))
        for line in response
        if line is not None and attribute in line.decode("utf-8", errors="replace")
    ]
    if len(matches) != 1:
        label = mailbox.value.title()
        raise EmailProviderError(
            f"Email provider did not expose exactly one {label} mailbox"
        )
    return matches[0]


def select_mailbox(connection: Any, mailbox: str, *, readonly: bool) -> int:
    """Select a mailbox and return its current UIDVALIDITY value."""
    status, _ = connection.select(mailbox, readonly=readonly)
    require_ok(status, "Could not open the email mailbox")
    try:
        response = connection.response("UIDVALIDITY")
    except (AttributeError, imaplib.IMAP4.error) as exc:
        raise EmailProviderError(
            "Email provider did not return mailbox UIDVALIDITY"
        ) from exc
    return uid_validity(response)


def source_reference(
    connection: Any,
    logical_mailbox: EmailMailbox,
    source_message_ref: str,
) -> ImapSourceReference:
    """Decode a source reference and require it to belong to the mailbox."""
    reference = decode_source_reference(source_message_ref)
    expected_mailbox = mailbox_for(connection, logical_mailbox)
    if reference.mailbox.casefold() != expected_mailbox.casefold():
        raise EmailProviderError(
            "email source does not belong to the requested mailbox"
        )
    return reference


def require_current_reference(actual: int, expected: int) -> None:
    """Reject a source reference when mailbox UID validity has changed."""
    if actual != expected:
        raise EmailProviderError("email source is stale; search email again")


def fetch_headers(connection: Any, uid: bytes) -> tuple[Message, datetime | None]:
    """Fetch bounded reply-relevant headers and receive time for one UID."""
    status, response = connection.uid(
        ImapUidCommand.FETCH,
        uid,
        f"(INTERNALDATE BODY.PEEK[HEADER.FIELDS ({_FETCH_HEADERS})])",
    )
    require_ok(status, "Could not read the email source")
    metadata, raw_headers = fetch_response_parts(response)
    if raw_headers is None:
        raise EmailProviderError("email source no longer exists")
    return parse_headers(raw_headers), internal_date(metadata)


def fetch_full_message(connection: Any, uid: bytes) -> tuple[bytes, bytes]:
    """Fetch one complete message while enforcing the configured size bound."""
    fetch_bytes = MAX_FULL_MESSAGE_BYTES + 1
    status, response = connection.uid(
        ImapUidCommand.FETCH,
        uid,
        f"(INTERNALDATE BODY.PEEK[]<0.{fetch_bytes}>)",
    )
    require_ok(status, "Could not read the email")
    metadata, raw_message = fetch_response_parts(response)
    if raw_message is None:
        raise EmailProviderError("email no longer exists")
    if len(raw_message) > MAX_FULL_MESSAGE_BYTES:
        raise EmailProviderError("email is larger than 25 MB")
    return metadata, raw_message
