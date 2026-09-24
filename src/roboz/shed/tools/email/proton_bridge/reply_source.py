"""Inbox reply recipients, threading headers, and optional quoted content."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..contracts import EmailMailbox, EmailProviderError
from .imap_codec import fetch_response_parts
from .imap_session import ImapSessionProvider, ensure_not_cancelled
from .mailbox import (
    fetch_headers,
    require_current_reference,
    select_mailbox,
    source_reference,
)
from .message_body import extract_message_body
from .message_headers import (
    display_header,
    reply_all_addresses,
    reply_references,
    single_message_id,
)
from .models import ReplySourceHeaders
from .protocol import (
    MAX_QUOTED_MESSAGE_FETCH_BYTES,
    MAX_REPLY_RECIPIENTS,
    EmailHeader,
    ImapResponseStatus,
    ImapUidCommand,
)


def fetch_reply_source(
    sessions: ImapSessionProvider,
    source_message_ref: str,
    *,
    own_addresses: tuple[str, ...],
    reply_all: bool,
    include_quoted_original: bool,
    is_cancelled: Callable[[], bool],
) -> ReplySourceHeaders:
    """Verify an Inbox source and derive safe reply headers."""
    ensure_not_cancelled(is_cancelled)
    with sessions.open() as connection:
        reference = source_reference(
            connection, EmailMailbox.INBOX, source_message_ref
        )
        validity = select_mailbox(connection, reference.mailbox, readonly=True)
        require_current_reference(validity, reference.uid_validity)
        headers, _ = fetch_headers(connection, reference.uid)
        recipients, cc = reply_all_addresses(
            headers,
            own_addresses=own_addresses,
            include_original_recipients=reply_all,
        )
        if not recipients:
            raise EmailProviderError(
                "email reply source has no valid reply address"
            )
        if len(recipients) + len(cc) > MAX_REPLY_RECIPIENTS:
            raise EmailProviderError(
                "email reply source has too many reply addresses"
            )
        message_id = single_message_id(headers.get(EmailHeader.MESSAGE_ID))
        if message_id is None:
            raise EmailProviderError(
                "email reply source has no valid Message-ID and cannot be threaded"
            )
        quoted_body: str | None = None
        quote_warnings: tuple[str, ...] = ()
        if include_quoted_original:
            ensure_not_cancelled(is_cancelled)
            quoted_body, quote_warnings = _fetch_quoted_body(
                connection, reference.uid
            )
        return ReplySourceHeaders(
            to=recipients,
            cc=cc,
            subject=display_header(
                headers, EmailHeader.SUBJECT, default="(no subject)"
            ),
            message_id=message_id,
            references=reply_references(headers, message_id),
            sender=display_header(headers, EmailHeader.FROM, max_chars=500),
            sent_at=display_header(
                headers, EmailHeader.DATE, default="", max_chars=500
            )
            or None,
            quoted_body=quoted_body,
            quote_warnings=quote_warnings,
        )


def _fetch_quoted_body(
    connection: Any, uid: bytes
) -> tuple[str | None, tuple[str, ...]]:
    fetch_bytes = MAX_QUOTED_MESSAGE_FETCH_BYTES + 1
    status, response = connection.uid(
        ImapUidCommand.FETCH,
        uid,
        f"(BODY.PEEK[]<0.{fetch_bytes}>)",
    )
    if status.upper() != ImapResponseStatus.OK:
        return None, ("The original message body could not be quoted.",)
    _, raw_message = fetch_response_parts(response)
    if raw_message is None:
        return None, ("The original message body could not be quoted.",)
    source_truncated = len(raw_message) > MAX_QUOTED_MESSAGE_FETCH_BYTES
    extracted = extract_message_body(
        raw_message[:MAX_QUOTED_MESSAGE_FETCH_BYTES],
        source_truncated=source_truncated,
    )
    if extracted.text is None:
        return None, ("The original message has no readable body to quote.",)
    warnings = (
        ("The quoted original message was truncated.",)
        if extracted.truncated
        else ()
    )
    return extracted.text, warnings
