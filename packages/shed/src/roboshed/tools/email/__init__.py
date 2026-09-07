"""Complete provider-neutral email tool bundle."""

from roboshed.email_inputs import (
    CreateEmailDraft,
    CreateReplyDraft,
    DownloadEmailAttachment,
    ReadEmail,
    SearchEmail,
)

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
