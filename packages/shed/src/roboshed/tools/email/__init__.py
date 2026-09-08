"""Complete provider-neutral email tool bundle."""

from .contracts import (
    DownloadedEmailAttachment,
    EmailDraftAttachment,
    EmailDraftRequest,
    EmailDraftResult,
    EmailInlineImage,
    EmailMailbox,
    EmailMessage,
    EmailMessageAttachment,
    EmailProviderError,
    EmailReplyDraftRequest,
    EmailReplyDraftResult,
    EmailSearchRequest,
    EmailService,
    EmailSignature,
    EmailSummary,
)
from .factory import get_work_with_email
from .inputs import (
    CreateEmailDraft,
    CreateReplyDraft,
    DownloadEmailAttachment,
    ReadEmail,
    SearchEmail,
)

__all__ = [
    "DownloadedEmailAttachment",
    "CreateEmailDraft",
    "CreateReplyDraft",
    "DownloadEmailAttachment",
    "EmailDraftAttachment",
    "EmailDraftRequest",
    "EmailDraftResult",
    "EmailInlineImage",
    "EmailMailbox",
    "EmailMessage",
    "EmailMessageAttachment",
    "EmailProviderError",
    "EmailReplyDraftRequest",
    "EmailReplyDraftResult",
    "EmailSearchRequest",
    "EmailService",
    "EmailSignature",
    "ReadEmail",
    "SearchEmail",
    "EmailSummary",
    "get_work_with_email",
]
