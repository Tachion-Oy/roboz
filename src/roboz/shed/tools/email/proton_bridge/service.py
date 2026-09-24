"""Proton Bridge email operations using verified, operation-local IMAP sessions."""

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date, datetime
from itertools import batched

from imaplib import IMAP4

from ..contracts import (
    DownloadedEmailAttachment,
    EmailDraftRequest,
    EmailDraftResult,
    EmailMailbox,
    EmailMessage,
    EmailProviderError,
    EmailReplyDraftRequest,
    EmailReplyDraftResult,
    EmailSearchRequest,
    EmailService,
    EmailSignature,
    EmailSummary,
)
from .connection import (
    HEADER_LIMIT,
    ClientFactory,
    MessageMissing,
    Session,
    default_client_factory,
    open_session,
)
from .content import (
    attachment_metadata,
    display_header,
    downloaded_attachment,
    extract_message_body,
    header_addresses,
    reply_addresses,
    reply_all_addresses,
    reply_references,
    single_message_id,
)
from .mime import build_draft_message, build_reply_draft_message, reply_subject
from .models import ProtonBridgeSettings, ReplySourceHeaders
from .references import decode_attachment_reference, encode_source_reference

REQUEST_ID_HEADER = "X-Roboz-Request-Id"


class ProtonBridgeEmailService(EmailService):
    """Create drafts and access bounded mail content; never send, move, or delete mail."""

    def __init__(
        self,
        settings: ProtonBridgeSettings,
        *,
        signature: EmailSignature | None = None,
        client_factory: ClientFactory = default_client_factory,
    ) -> None:
        """Accept explicit settings and an optional IMAPClient transport factory.

        Construction performs no I/O. Each operation owns its connection.
        """
        self._settings = settings
        self._signature = signature
        self._client_factory = client_factory

    def _session(
        self, is_cancelled: Callable[[], bool]
    ) -> AbstractContextManager[Session]:
        return open_session(self._settings, self._client_factory, is_cancelled)

    @property
    def dependency_id(self) -> str:
        """Return the stable network dependency identity."""
        return "network:proton_bridge"

    def redacted_metadata(self) -> dict[str, str]:
        """Describe the connection without exposing credentials."""
        return {
            "provider": "proton_bridge",
            "host": self._settings.imap_host,
            "port": str(self._settings.imap_port),
            "tls_mode": self._settings.tls_mode.value,
        }

    def probe(self) -> dict[str, object]:
        """Authenticate and discover special mailboxes without changing mail."""
        with self._session(lambda: False) as session:
            return {
                "provider": "proton_bridge",
                "imap_host": self._settings.imap_host,
                "imap_port": self._settings.imap_port,
                "tls_mode": self._settings.tls_mode,
                "drafts_mailbox": session.mailbox(EmailMailbox.DRAFTS),
                "sent_mailbox": session.mailbox(EmailMailbox.SENT),
            }

    def create_draft(
        self, request: EmailDraftRequest, *, is_cancelled: Callable[[], bool]
    ) -> EmailDraftResult:
        """Persist a draft, optionally reusing an exact request-ID match."""
        with self._session(is_cancelled) as session:
            session.check()
            message = build_draft_message(
                request,
                default_from_address=self._settings.account_address,
                signature=self._signature,
            )
            draft_id, warnings = self._store(
                session, message, request.client_request_id
            )
            return EmailDraftResult(
                draft_id,
                request.from_address or self._settings.account_address,
                warnings,
            )

    def _store(
        self, session: Session, message: bytes, request_id: str | None
    ) -> tuple[str, tuple[str, ...]]:
        mailbox = session.mailbox(EmailMailbox.DRAFTS)
        warnings = self._signature.warnings if self._signature else ()
        if request_id:
            session.select(mailbox)
            for uid in reversed(
                session.search(["HEADER", REQUEST_ID_HEADER, request_id])
            ):
                try:
                    headers, _, _ = session.message(
                        uid, section="HEADER", limit=HEADER_LIMIT
                    )
                except MessageMissing:
                    continue
                if headers.get_all(REQUEST_ID_HEADER, []) == [request_id]:
                    return f"imap-uid:{uid}", (
                        *warnings,
                        "Reused an existing draft for this request id.",
                    )
        session.check()  # The duplicate search may have outlived the caller.
        try:
            response: bytes = session.client.append(
                mailbox, message, flags=[b"\\Draft"]
            )
        except (OSError, IMAP4.abort) as exc:
            raise EmailProviderError(
                "Draft creation outcome is unknown; inspect Drafts before retrying"
            ) from exc
        match = re.search(rb"\bAPPENDUID [1-9][0-9]* ([1-9][0-9]*)\b", response)
        if match:
            return f"imap-uid:{int(match[1])}", warnings
        return f"imap:{mailbox}:unknown", (
            *warnings,
            "The provider did not return a draft UID; verify the Drafts folder before retrying.",
        )

    def search_messages(
        self, request: EmailSearchRequest, *, is_cancelled: Callable[[], bool]
    ) -> tuple[EmailSummary, ...]:
        """Search by structured filters and return newest-first bounded summaries."""
        with self._session(is_cancelled) as session:
            mailbox = session.mailbox(request.mailbox)
            validity = session.select(mailbox)
            criteria: list[str | date] = []
            for key, value in (
                ("FROM", request.from_address),
                ("TO", request.to_address),
                ("SUBJECT", request.subject_contains),
                ("TEXT", request.text_contains),
                ("SINCE", request.since),
                ("BEFORE", request.before),
            ):
                if value:
                    criteria.extend((key, value))
            uids = session.search(criteria or ["ALL"])
            ordered: list[tuple[datetime, int, bool]] = []
            for batch in batched(uids, 500):
                for uid, fields in session.data(
                    batch, ["INTERNALDATE", "FLAGS"]
                ).items():
                    timestamp, flags = fields.get(b"INTERNALDATE"), fields.get(b"FLAGS")
                    if not isinstance(timestamp, datetime) or not isinstance(
                        flags, tuple
                    ):
                        raise EmailProviderError(
                            "Email provider returned invalid search metadata"
                        )
                    ordered.append((timestamp, uid, b"\\Seen" in flags))
            summaries: list[EmailSummary] = []
            for timestamp, uid, is_read in sorted(ordered, reverse=True):
                if len(summaries) >= request.limit:
                    break
                try:
                    headers, _, _ = session.message(
                        uid, section="HEADER", limit=HEADER_LIMIT
                    )
                    preview, _, truncated = session.message(
                        uid, limit=16_384, truncate=True
                    )
                except MessageMissing:
                    continue
                summaries.append(
                    EmailSummary(
                        source_message_ref=encode_source_reference(
                            mailbox, validity, uid
                        ),
                        mailbox=request.mailbox,
                        sender=display_header(headers, "From", max_chars=500),
                        to=header_addresses(headers, "To"),
                        subject=display_header(
                            headers, "Subject", default="(no subject)", max_chars=500
                        ),
                        timestamp=timestamp,
                        is_read=is_read,
                        replyable=(
                            request.mailbox is EmailMailbox.INBOX
                            and single_message_id(headers.get("Message-ID")) is not None
                            and 0 < len(reply_addresses(headers)) <= 50
                        ),
                        preview_text=extract_message_body(
                            preview, source_truncated=truncated, max_chars=160
                        ).text,
                    )
                )
            return tuple(summaries)

    def read_message(
        self,
        mailbox: EmailMailbox,
        source_message_ref: str,
        *,
        is_cancelled: Callable[[], bool],
    ) -> EmailMessage:
        """Read a bounded message; mark Inbox seen only after successful parsing."""
        with self._session(is_cancelled) as session:
            uid = session.source(
                source_message_ref, mailbox, readonly=mailbox is not EmailMailbox.INBOX
            )
            message, timestamp, _ = session.message(uid)
            body = extract_message_body(message, source_truncated=False)
            result = EmailMessage(
                source_message_ref=source_message_ref,
                mailbox=mailbox,
                sender=display_header(message, "From", max_chars=500),
                to=header_addresses(message, "To"),
                cc=header_addresses(message, "Cc"),
                bcc=header_addresses(message, "Bcc"),
                subject=display_header(
                    message, "Subject", default="(no subject)", max_chars=500
                ),
                timestamp=timestamp,
                body_text=body.text,
                attachments=attachment_metadata(message, source_message_ref),
                truncated=body.truncated,
                warnings=("The readable message body was truncated.",)
                if body.truncated
                else (),
            )
            if mailbox is EmailMailbox.INBOX:
                session.check()
                session.client.add_flags([uid], [b"\\Seen"], silent=True)
            return result

    def download_attachment(
        self, attachment_ref: str, *, is_cancelled: Callable[[], bool]
    ) -> DownloadedEmailAttachment:
        """Resolve an allowed mailbox and download one bounded MIME attachment."""
        reference = decode_attachment_reference(attachment_ref)
        with self._session(is_cancelled) as session:
            uid = session.source(reference.source_message_ref, None)
            message, _, _ = session.message(uid)
            return downloaded_attachment(message, reference.attachment_index)

    def create_reply_draft(
        self, request: EmailReplyDraftRequest, *, is_cancelled: Callable[[], bool]
    ) -> EmailReplyDraftResult:
        """Derive Inbox recipients and threading, then persist an optional quoted reply."""
        from_address = request.from_address or self._settings.account_address
        with self._session(is_cancelled) as session:
            uid = session.source(request.source_message_ref, EmailMailbox.INBOX)
            headers, _, _ = session.message(uid, section="HEADER", limit=HEADER_LIMIT)
            to, cc = reply_all_addresses(
                headers,
                own_addresses=(self._settings.account_address, from_address),
                include_original_recipients=request.reply_all,
            )
            if not to or len(to) + len(cc) > 50:
                raise EmailProviderError(
                    "email reply source has no valid reply address or too many reply addresses"
                )
            message_id = single_message_id(headers.get("Message-ID"))
            if message_id is None:
                raise EmailProviderError(
                    "email reply source has no valid Message-ID and cannot be threaded"
                )
            quoted_body, quote_warnings = None, ()
            if request.include_quoted_original:
                original, _, truncated = session.message(
                    uid, limit=1_000_000, truncate=True
                )
                body = extract_message_body(original, source_truncated=truncated)
                quoted_body = body.text
                if body.text is None:
                    quote_warnings = (
                        "The original message has no readable body to quote.",
                    )
                elif body.truncated:
                    quote_warnings = ("The quoted original message was truncated.",)
            source = ReplySourceHeaders(
                to=to,
                cc=cc,
                subject=display_header(headers, "Subject", default="(no subject)"),
                message_id=message_id,
                references=reply_references(headers, message_id),
                sender=display_header(headers, "From", max_chars=500),
                sent_at=display_header(headers, "Date", default="", max_chars=500)
                or None,
                quoted_body=quoted_body,
            )
            session.check()
            message = build_reply_draft_message(
                request,
                source,
                default_from_address=self._settings.account_address,
                signature=self._signature,
            )
            draft_id, warnings = self._store(
                session, message, request.client_request_id
            )
            return EmailReplyDraftResult(
                draft_id=draft_id,
                account_address=from_address,
                to=to,
                cc=cc,
                subject=reply_subject(source.subject),
                quoted_original_included=quoted_body is not None,
                warnings=(*quote_warnings, *warnings),
            )
