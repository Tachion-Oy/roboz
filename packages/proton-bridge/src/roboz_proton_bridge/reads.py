"""Bounded full-message reads and attachment retrieval."""

from __future__ import annotations

from collections.abc import Callable

from roboz_shed.tools.email.contracts import (
    DownloadedEmailAttachment,
    EmailMailbox,
    EmailMessage,
    EmailProviderError,
)
from .attachments import (
    attachment_metadata,
    downloaded_attachment,
    parse_message,
)
from .imap_codec import decode_attachment_reference, internal_date, require_ok
from .imap_session import ImapSessionProvider, ensure_not_cancelled
from .mailbox import (
    fetch_full_message,
    require_current_reference,
    select_mailbox,
    source_reference,
)
from .message_body import extract_message_body
from .message_headers import display_header, header_addresses
from .protocol import EmailHeader, ImapSystemFlag, ImapUidCommand


def read_message(
    sessions: ImapSessionProvider,
    mailbox: EmailMailbox,
    source_message_ref: str,
    *,
    is_cancelled: Callable[[], bool],
) -> EmailMessage:
    ensure_not_cancelled(is_cancelled)
    with sessions.open() as connection:
        reference = source_reference(connection, mailbox, source_message_ref)
        validity = select_mailbox(
            connection,
            reference.mailbox,
            readonly=mailbox is not EmailMailbox.INBOX,
        )
        require_current_reference(validity, reference.uid_validity)
        metadata, raw_message = fetch_full_message(connection, reference.uid)
        parsed = parse_message(raw_message)
        extracted = extract_message_body(raw_message, source_truncated=False)
        if mailbox is EmailMailbox.INBOX:
            ensure_not_cancelled(is_cancelled)
            status, _ = connection.uid(
                ImapUidCommand.STORE,
                reference.uid,
                "+FLAGS.SILENT",
                f"({ImapSystemFlag.SEEN})",
            )
            require_ok(status, "Could not mark the email as read")
        warnings = (
            ("The readable message body was truncated.",) if extracted.truncated else ()
        )
        return EmailMessage(
            source_message_ref=source_message_ref,
            mailbox=mailbox,
            sender=display_header(parsed, EmailHeader.FROM, max_chars=500),
            to=header_addresses(parsed, EmailHeader.TO),
            cc=header_addresses(parsed, EmailHeader.CC),
            bcc=header_addresses(parsed, EmailHeader.BCC),
            subject=display_header(
                parsed, EmailHeader.SUBJECT, default="(no subject)", max_chars=500
            ),
            timestamp=internal_date(metadata),
            body_text=extracted.text,
            attachments=attachment_metadata(parsed, source_message_ref),
            truncated=extracted.truncated,
            warnings=warnings,
        )


def download_attachment(
    sessions: ImapSessionProvider,
    attachment_ref: str,
    *,
    is_cancelled: Callable[[], bool],
) -> DownloadedEmailAttachment:
    decoded = decode_attachment_reference(attachment_ref)
    ensure_not_cancelled(is_cancelled)
    with sessions.open() as connection:
        # Attachment references are only issued by reads of these three mailboxes.
        for mailbox in EmailMailbox:
            try:
                reference = source_reference(
                    connection, mailbox, decoded.source_message_ref
                )
                break
            except EmailProviderError:
                continue
        else:
            raise EmailProviderError("email attachment source is invalid")
        validity = select_mailbox(connection, reference.mailbox, readonly=True)
        require_current_reference(validity, reference.uid_validity)
        _, raw_message = fetch_full_message(connection, reference.uid)
        return downloaded_attachment(
            parse_message(raw_message), decoded.attachment_index
        )
