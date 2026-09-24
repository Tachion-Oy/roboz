"""Concrete Proton Bridge email service and tool bundle."""

from __future__ import annotations

from collections.abc import Callable

from ..contracts import (
    DownloadedEmailAttachment,
    EmailDraftRequest,
    EmailDraftResult,
    EmailMailbox,
    EmailMessage,
    EmailReplyDraftRequest,
    EmailReplyDraftResult,
    EmailSearchRequest,
    EmailService,
    EmailSignature,
    EmailSummary,
)
from .draft_store import probe_mailboxes, store_draft
from .imap_session import ImapFactory, ImapSessionProvider, default_imap_factory
from .mime import build_draft_message, build_reply_draft_message, reply_subject
from .models import ProtonBridgeSettings
from .reads import download_attachment, read_message
from .reply_source import fetch_reply_source
from .search import search_messages


class ProtonBridgeEmailService(EmailService):
    """Proton Bridge mailbox service with explicit dependency identity and probe."""

    def __init__(
        self,
        settings: ProtonBridgeSettings,
        *,
        signature: EmailSignature | None = None,
        imap_factory: ImapFactory = default_imap_factory,
    ) -> None:
        """Store caller-provided settings and the injectable IMAP transport."""
        self._settings = settings
        self._signature = signature
        self._imap_factory = imap_factory

    def create_draft(
        self,
        request: EmailDraftRequest,
        *,
        is_cancelled: Callable[[], bool],
    ) -> EmailDraftResult:
        """Create a server-side draft without sending it."""
        settings = self._settings
        signature = self._signature
        message = build_draft_message(
            request,
            default_from_address=settings.account_address,
            signature=signature,
        )
        draft_id, warnings = store_draft(
            self._sessions(settings),
            message,
            request_id=request.client_request_id,
            is_cancelled=is_cancelled,
        )
        return EmailDraftResult(
            draft_id=draft_id,
            account_address=request.from_address or settings.account_address,
            warnings=(*(signature.warnings if signature else ()), *warnings),
        )

    def search_messages(
        self,
        request: EmailSearchRequest,
        *,
        is_cancelled: Callable[[], bool],
    ) -> tuple[EmailSummary, ...]:
        """Search one logical mailbox for bounded message summaries."""
        settings = self._settings
        return search_messages(
            self._sessions(settings), request, is_cancelled=is_cancelled
        )

    def read_message(
        self,
        mailbox: EmailMailbox,
        source_message_ref: str,
        *,
        is_cancelled: Callable[[], bool],
    ) -> EmailMessage:
        """Read one message and mark successful Inbox reads as seen."""
        settings = self._settings
        return read_message(
            self._sessions(settings),
            mailbox,
            source_message_ref,
            is_cancelled=is_cancelled,
        )

    def download_attachment(
        self,
        attachment_ref: str,
        *,
        is_cancelled: Callable[[], bool],
    ) -> DownloadedEmailAttachment:
        """Download one attachment after checking its source reference."""
        settings = self._settings
        return download_attachment(
            self._sessions(settings),
            attachment_ref,
            is_cancelled=is_cancelled,
        )

    def create_reply_draft(
        self,
        request: EmailReplyDraftRequest,
        *,
        is_cancelled: Callable[[], bool],
    ) -> EmailReplyDraftResult:
        """Create a threaded server-side reply draft without sending it."""
        settings = self._settings
        signature = self._signature
        from_address = request.from_address or settings.account_address
        sessions = self._sessions(settings)
        source = fetch_reply_source(
            sessions,
            request.source_message_ref,
            own_addresses=(settings.account_address, from_address),
            reply_all=request.reply_all,
            include_quoted_original=request.include_quoted_original,
            is_cancelled=is_cancelled,
        )
        message = build_reply_draft_message(
            request,
            source,
            default_from_address=settings.account_address,
            signature=signature,
        )
        draft_id, store_warnings = store_draft(
            sessions,
            message,
            request_id=request.client_request_id,
            is_cancelled=is_cancelled,
        )
        return EmailReplyDraftResult(
            draft_id=draft_id,
            account_address=from_address,
            to=source.to,
            subject=reply_subject(source.subject),
            cc=source.cc,
            quoted_original_included=source.quoted_body is not None,
            warnings=(
                *(signature.warnings if signature else ()),
                *source.quote_warnings,
                *store_warnings,
            ),
        )

    @property
    def dependency_id(self) -> str:
        """Return the stable Proton Bridge network dependency ID."""
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
        """Authenticate and inspect mailboxes without changing messages."""
        settings = self._settings
        return probe_mailboxes(self._sessions(settings))

    def _sessions(self, settings: ProtonBridgeSettings) -> ImapSessionProvider:
        return ImapSessionProvider(settings, imap_factory=self._imap_factory)
