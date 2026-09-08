"""Provider-neutral email values and external-service contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from roboz.dependencies import NetworkServiceDependency


class EmailMailbox(StrEnum):
    """Logical mailboxes exposed by the email tools."""

    INBOX = "inbox"
    DRAFTS = "drafts"
    SENT = "sent"


@dataclass(frozen=True)
class EmailDraftAttachment:
    """A local file approved for attachment after path resolution and READ guard."""

    path: str
    filename: str


@dataclass(frozen=True)
class EmailInlineImage:
    """An inline image embedded in provider-composed email HTML."""

    data: bytes
    filename: str
    content_type: str
    content_id: str


@dataclass(frozen=True)
class EmailSignature:
    """Trusted deployment-owned signature content for every created draft."""

    plain_text: str
    html: str
    inline_image: EmailInlineImage | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmailDraftRequest:
    """Normalized email content that is safe to hand to a service."""

    to: tuple[str, ...]
    cc: tuple[str, ...]
    bcc: tuple[str, ...]
    subject: str
    body_text: str
    from_address: str | None
    reply_to: str | None
    client_request_id: str | None
    attachments: tuple[EmailDraftAttachment, ...] = ()


@dataclass(frozen=True)
class EmailDraftResult:
    """Service-neutral receipt for a draft that exists server-side."""

    draft_id: str
    account_address: str | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmailSearchRequest:
    """Provider-neutral, structured search over one logical mailbox."""

    mailbox: EmailMailbox = EmailMailbox.INBOX
    from_address: str | None = None
    to_address: str | None = None
    subject_contains: str | None = None
    text_contains: str | None = None
    since: date | None = None
    before: date | None = None
    limit: int = 10


@dataclass(frozen=True)
class EmailSummary:
    """Bounded metadata for one message."""

    source_message_ref: str
    mailbox: EmailMailbox
    sender: str
    to: tuple[str, ...]
    subject: str
    timestamp: datetime | None
    replyable: bool
    is_read: bool = False
    preview_text: str | None = None


@dataclass(frozen=True)
class EmailMessageAttachment:
    """Bounded metadata and opaque reference for one message attachment."""

    attachment_ref: str
    filename: str
    content_type: str
    size: int


@dataclass(frozen=True)
class EmailMessage:
    """Sanitized, bounded content returned by a message read."""

    source_message_ref: str
    mailbox: EmailMailbox
    sender: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    bcc: tuple[str, ...]
    subject: str
    timestamp: datetime | None
    body_text: str | None
    attachments: tuple[EmailMessageAttachment, ...] = ()
    truncated: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DownloadedEmailAttachment:
    """Decoded attachment bytes plus safe display metadata."""

    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class EmailReplyDraftRequest:
    """Normalized reply content; source-derived headers are intentionally absent."""

    source_message_ref: str
    body_text: str
    from_address: str | None
    client_request_id: str | None
    include_quoted_original: bool = True
    reply_all: bool = True
    attachments: tuple[EmailDraftAttachment, ...] = ()


@dataclass(frozen=True)
class EmailReplyDraftResult:
    """Receipt and source-derived display metadata for a persisted reply draft."""

    draft_id: str
    account_address: str | None
    to: tuple[str, ...]
    subject: str
    cc: tuple[str, ...] = ()
    quoted_original_included: bool = False
    warnings: tuple[str, ...] = ()


class EmailProviderError(RuntimeError):
    """A safe, user-presentable email-provider failure."""


class EmailService(NetworkServiceDependency, ABC):
    """Complete provider contract for every operation in the email bundle."""

    @abstractmethod
    def create_draft(
        self, request: EmailDraftRequest, *, is_cancelled: Callable[[], bool]
    ) -> EmailDraftResult:
        """Create but do not send a new email draft."""
        ...

    @abstractmethod
    def probe(self) -> dict[str, object]:
        """Perform an authenticated, read-only service availability probe."""
        ...

    @abstractmethod
    def search_messages(
        self,
        request: EmailSearchRequest,
        *,
        is_cancelled: Callable[[], bool],
    ) -> tuple[EmailSummary, ...]:
        """Search one mailbox and return bounded message summaries."""
        ...

    @abstractmethod
    def read_message(
        self,
        mailbox: EmailMailbox,
        source_message_ref: str,
        *,
        is_cancelled: Callable[[], bool],
    ) -> EmailMessage:
        """Read one referenced message from its logical mailbox."""
        ...

    @abstractmethod
    def download_attachment(
        self,
        attachment_ref: str,
        *,
        is_cancelled: Callable[[], bool],
    ) -> DownloadedEmailAttachment:
        """Download the bytes and safe metadata for one attachment reference."""
        ...

    @abstractmethod
    def create_reply_draft(
        self,
        request: EmailReplyDraftRequest,
        *,
        is_cancelled: Callable[[], bool],
    ) -> EmailReplyDraftResult:
        """Create but do not send a reply to one referenced message."""
        ...
