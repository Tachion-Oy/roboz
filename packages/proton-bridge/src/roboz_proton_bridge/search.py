"""Mailbox search, ordering, and bounded previews."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from email.message import Message
from itertools import batched
from typing import Any

from roboshed.tools.email.contracts import (
    EmailMailbox,
    EmailProviderError,
    EmailSearchRequest,
    EmailSummary,
)
from .imap_codec import (
    encode_source_reference,
    fetch_response_parts,
    require_ok,
    search_criteria,
    uid_message_metadata,
)
from .imap_session import ImapSessionProvider, ensure_not_cancelled
from .mailbox import fetch_headers, mailbox_for, select_mailbox
from .message_body import extract_message_body
from .message_headers import (
    display_header,
    header_addresses,
    reply_addresses,
    single_message_id,
)
from .protocol import (
    IMAP_SEARCH_CHARSET,
    IMAP_SEARCH_CHARSET_ARGUMENT,
    MAX_REPLY_RECIPIENTS,
    MAX_SEARCH_METADATA_CANDIDATES,
    MAX_SEARCH_PREVIEW_CHARS,
    MAX_SEARCH_PREVIEW_FETCH_BYTES,
    SEARCH_METADATA_BATCH_SIZE,
    EmailHeader,
    ImapResponseStatus,
    ImapUidCommand,
)


def search_messages(
    sessions: ImapSessionProvider,
    request: EmailSearchRequest,
    *,
    is_cancelled: Callable[[], bool],
) -> tuple[EmailSummary, ...]:
    """Search one mailbox and return newest-first bounded summaries."""
    ensure_not_cancelled(is_cancelled)
    with sessions.open() as connection:
        physical_mailbox = mailbox_for(connection, request.mailbox)
        validity = select_mailbox(connection, physical_mailbox, readonly=True)
        status, response = connection.uid(
            ImapUidCommand.SEARCH,
            IMAP_SEARCH_CHARSET_ARGUMENT,
            IMAP_SEARCH_CHARSET,
            *search_criteria(request),
        )
        require_ok(status, f"Could not search {request.mailbox.value} email")
        raw = response[0] if response else b""
        uids = raw.split() if isinstance(raw, bytes) else []
        ordered = _order_uids(connection, uids, is_cancelled=is_cancelled)
        results: list[EmailSummary] = []
        for uid, ordered_at, is_read in ordered:
            if len(results) >= request.limit:
                break
            ensure_not_cancelled(is_cancelled)
            try:
                headers, timestamp = fetch_headers(connection, uid)
                results.append(
                    _summary(
                        headers,
                        timestamp or ordered_at,
                        request.mailbox,
                        physical_mailbox,
                        validity,
                        uid,
                        is_read=is_read,
                        preview_text=_fetch_preview(connection, uid),
                    )
                )
            except EmailProviderError:
                continue
        return tuple(results)


def _order_uids(
    connection: Any,
    uids: list[bytes],
    *,
    is_cancelled: Callable[[], bool],
) -> tuple[tuple[bytes, datetime, bool], ...]:
    valid_uids = tuple(
        uid
        for uid in uids
        if uid.isascii() and uid.isdigit() and not uid.startswith(b"0")
    )
    if not valid_uids:
        return ()
    if len(valid_uids) > MAX_SEARCH_METADATA_CANDIDATES:
        raise EmailProviderError("Too many emails matched; add more search filters")
    metadata: dict[bytes, tuple[datetime, bool]] = {}
    for batch in batched(valid_uids, SEARCH_METADATA_BATCH_SIZE):
        ensure_not_cancelled(is_cancelled)
        requested = set(batch)
        status, response = connection.uid(
            ImapUidCommand.FETCH,
            b",".join(batch),
            "(UID INTERNALDATE FLAGS)",
        )
        require_ok(status, "Could not order email search results")
        for uid, timestamp, is_read in uid_message_metadata(response):
            if uid in requested:
                metadata[uid] = (timestamp, is_read)
    if not metadata:
        raise EmailProviderError(
            "Email provider did not return dates for matching messages"
        )
    return tuple(
        sorted(
            (
                (uid, timestamp, is_read)
                for uid, (timestamp, is_read) in metadata.items()
            ),
            key=lambda item: (item[1], int(item[0])),
            reverse=True,
        )
    )


def _fetch_preview(connection: Any, uid: bytes) -> str | None:
    fetch_bytes = MAX_SEARCH_PREVIEW_FETCH_BYTES + 1
    status, response = connection.uid(
        ImapUidCommand.FETCH,
        uid,
        f"(BODY.PEEK[]<0.{fetch_bytes}>)",
    )
    if status.upper() != ImapResponseStatus.OK:
        return None
    _, raw_message = fetch_response_parts(response)
    if raw_message is None:
        return None
    extracted = extract_message_body(
        raw_message[:MAX_SEARCH_PREVIEW_FETCH_BYTES],
        source_truncated=len(raw_message) > MAX_SEARCH_PREVIEW_FETCH_BYTES,
        max_chars=MAX_SEARCH_PREVIEW_CHARS,
    )
    return extracted.text


def _summary(
    headers: Message,
    timestamp: datetime | None,
    mailbox: EmailMailbox,
    physical_mailbox: str,
    validity: int,
    uid: bytes,
    *,
    is_read: bool,
    preview_text: str | None,
) -> EmailSummary:
    replyable = False
    if mailbox is EmailMailbox.INBOX:
        message_id = single_message_id(headers.get(EmailHeader.MESSAGE_ID))
        recipients = reply_addresses(headers)
        replyable = (
            message_id is not None and 0 < len(recipients) <= MAX_REPLY_RECIPIENTS
        )
    return EmailSummary(
        source_message_ref=encode_source_reference(physical_mailbox, validity, uid),
        mailbox=mailbox,
        sender=display_header(headers, EmailHeader.FROM, max_chars=500),
        to=header_addresses(headers, EmailHeader.TO),
        subject=display_header(
            headers,
            EmailHeader.SUBJECT,
            default="(no subject)",
            max_chars=500,
        ),
        timestamp=timestamp,
        replyable=replyable,
        is_read=is_read,
        preview_text=preview_text,
    )
