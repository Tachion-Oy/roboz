"""Validate agent email input and resolve optional attachment paths."""

import re
from email.headerregistry import Address
from pathlib import Path

from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallInterruptedError,
    ExternalCallTimeoutError,
)
from roboz.models import Message, Str
from roboz.models.truncation import Severity, Truncation
from roboz.tooling.decorators import factory

from roboz_shed.email_inputs import (
    CreateEmailDraft,
    CreateReplyDraft,
    DownloadEmailAttachment,
    SearchEmail,
)
from roboz_shed.models import (
    EmailAttachmentDownloadReady,
    EmailReady,
    GuardFileSingle,
    GuardFilesResult,
    Operation,
    ParseError,
)
from roboz_shed.tools.email.contracts import (
    EmailDraftAttachment,
    EmailDraftRequest,
    EmailMailbox,
    EmailProviderError,
    EmailReplyDraftRequest,
    EmailSearchRequest,
)
from roboz_shed.tools.types import EmailToolCtx, ResolvedFileCommand
from roboz_shed.tools.utils import resolve_single_file_path

from .runtime import EmailRuntimeContext, run_email_call


def resolve_draft_request(
    input: CreateEmailDraft,
    *,
    attachments: tuple[EmailDraftAttachment, ...] = (),
) -> EmailDraftRequest:
    """Normalize and validate mail headers and recipients.

    Header line breaks are rejected before MIME serialization, preventing an
    agent-provided field from adding arbitrary headers.
    """

    return EmailDraftRequest(
        to=_addresses(input.to, "to"),
        cc=_addresses(input.cc, "cc"),
        bcc=_addresses(input.bcc, "bcc"),
        subject=_header(input.subject, "subject"),
        body_text=_body(input.body_text),
        from_address=_optional_address(input.from_address, "from_address"),
        reply_to=_optional_address(input.reply_to, "reply_to"),
        client_request_id=_request_id(input.client_request_id),
        attachments=attachments,
    )


def resolve_search_request(input: SearchEmail) -> EmailSearchRequest:
    """Normalize structured search fields without exposing raw IMAP syntax."""

    since = input.since
    before = input.before
    if since is not None and before is not None and since >= before:
        raise ValueError("since must be earlier than before")
    return EmailSearchRequest(
        mailbox=EmailMailbox(input.mailbox),
        from_address=_optional_address(input.from_address, "from_address"),
        to_address=_optional_address(input.to_address, "to_address"),
        subject_contains=_optional_search_text(
            input.subject_contains, "subject_contains"
        ),
        text_contains=_optional_search_text(input.text_contains, "text_contains"),
        since=since,
        before=before,
        limit=input.limit,
    )


def resolve_reply_draft_request(
    input: CreateReplyDraft,
    *,
    attachments: tuple[EmailDraftAttachment, ...] = (),
) -> EmailReplyDraftRequest:
    """Normalize a reply body while leaving all source-derived headers absent."""

    return EmailReplyDraftRequest(
        source_message_ref=_header(input.source_message_ref, "source_message_ref"),
        body_text=_body(input.body_text),
        from_address=_optional_address(input.from_address, "from_address"),
        client_request_id=_request_id(input.client_request_id),
        include_quoted_original=input.include_quoted_original,
        reply_all=input.reply_all,
        attachments=attachments,
    )


def _optional_search_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if any(character in normalized for character in ("\r", "\n", "\x00")):
        raise ValueError(f"{field} must not contain control characters")
    return normalized


def _addresses(values: list[str], field: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        address = _address(raw, field)
        key = address.casefold()
        if key not in seen:
            normalized.append(address)
            seen.add(key)
    if field == "to" and not normalized:
        raise ValueError("at least one primary recipient is required")
    return tuple(normalized)


def _optional_address(value: str | None, field: str) -> str | None:
    return None if value is None else _address(value, field)


def _address(raw: str, field: str) -> str:
    value = _header(raw, field)
    try:
        address = Address(addr_spec=value).addr_spec
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid email address") from exc
    if "@" not in address or address.startswith("@") or address.endswith("@"):
        raise ValueError(f"{field} must be a valid email address")
    return address


def _request_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _header(value, "client_request_id")
    if re.fullmatch(r"[A-Za-z0-9._:-]+", normalized) is None:
        raise ValueError(
            "client_request_id may contain only letters, numbers, dot, underscore, colon, or hyphen"
        )
    return normalized


def _header(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if "\r" in normalized or "\n" in normalized:
        raise ValueError(f"{field} must not contain line breaks")
    return normalized


def _body(value: str) -> str:
    if "\x00" in value:
        raise ValueError("body_text must not contain NUL bytes")
    if not value.strip():
        raise ValueError("body_text must not be empty")
    return value


def _resolve_attachment_path(raw_path: str, *, base: Path) -> Path:
    location = resolve_single_file_path(raw_path, base=base, label="attachment_paths")
    if not location.is_file():
        raise ValueError("attachment_paths must refer to an existing regular file")
    return location


def _resolve_attachment_items(
    attachment_paths: list[str], *, base: Path
) -> list[GuardFileSingle]:
    items: list[GuardFileSingle] = []
    seen: set[Path] = set()
    for raw_path in attachment_paths:
        location = _resolve_attachment_path(raw_path, base=base)
        if location in seen:
            continue
        seen.add(location)
        items.append(
            GuardFileSingle(
                operation=Operation.READ,
                location=location,
                value=EmailReady(
                    attachment_path=str(location),
                    attachment_filename=location.name,
                ),
            )
        )
    return items


@factory
def resolve_email_input(
    input: CreateEmailDraft, messages: list[Message], ctx: EmailToolCtx
) -> ResolvedFileCommand | ParseError:
    """Validate draft fields and resolve attachments for READ guard."""

    del messages
    try:
        resolve_draft_request(input)
        base = ctx.base.resolve()
        items = _resolve_attachment_items(input.attachment_paths, base=base)
        return ResolvedFileCommand(original_input=input, items=items)
    except ValueError as exc:
        return ParseError(
            message=f"Invalid email draft: {exc}",
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        )


@factory
def resolve_reply_draft_input(
    input: CreateReplyDraft, messages: list[Message], ctx: EmailToolCtx
) -> ResolvedFileCommand | ParseError:
    """Validate reply fields and resolve attachments for the shared READ guard."""

    del messages
    try:
        resolve_reply_draft_request(input)
        items = _resolve_attachment_items(
            input.attachment_paths, base=ctx.base.resolve()
        )
        return ResolvedFileCommand(original_input=input, items=items)
    except ValueError as exc:
        return ParseError(
            message=f"Invalid email reply draft: {exc}",
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        )


@factory
def resolve_attachment_download(
    input: DownloadEmailAttachment,
    messages: list[Message],
    ctx: EmailToolCtx,
) -> ResolvedFileCommand | ParseError:
    """Resolve one explicit destination for the shared CREATE guard."""

    del messages
    try:
        destination = resolve_single_file_path(
            input.destination_path,
            base=ctx.base.resolve(),
            label="destination_path",
        )
        if destination.exists() and not destination.is_file():
            raise ValueError("destination_path must refer to a regular file")
        if not destination.parent.is_dir():
            raise ValueError("destination_path parent directory must exist")
        return ResolvedFileCommand(
            original_input=input,
            items=[
                GuardFileSingle(
                    operation=Operation.CREATE,
                    location=destination,
                    value=EmailAttachmentDownloadReady(
                        attachment_ref=_header(input.attachment_ref, "attachment_ref")
                    ),
                )
            ],
        )
    except ValueError as exc:
        return ParseError(
            message=f"Invalid attachment download: {exc}",
            truncation=Truncation(threshold=0, severity=Severity.LIGHT),
        )


def _attachments_from_guard(
    input: GuardFilesResult,
) -> tuple[EmailDraftAttachment, ...]:
    attachments: list[EmailDraftAttachment] = []
    for item in input.items:
        payload = item.value
        if not isinstance(payload, EmailReady):
            raise EmailProviderError("unexpected guarded email attachment payload")
        attachments.append(
            EmailDraftAttachment(
                path=payload.attachment_path,
                filename=payload.attachment_filename,
            )
        )
    return tuple(attachments)


@factory
def execute_email_operation(
    input: GuardFilesResult,
    messages: list[Message],
    ctx: EmailRuntimeContext,
) -> Str:
    del messages
    try:
        original = input.original_input
        if not isinstance(original, CreateEmailDraft):
            raise EmailProviderError("unsupported email operation")
        request = resolve_draft_request(
            original, attachments=_attachments_from_guard(input)
        )
        result = run_email_call(
            ctx,
            label="email-create-draft",
            cancelled_message="Email draft creation was cancelled",
            operation=lambda: ctx.service.resource.create_draft(
                request, is_cancelled=ctx.is_cancelled
            ),
        )
        recipients = ", ".join(request.to)
        cc = f"; cc: {', '.join(request.cc)}" if request.cc else ""
        account = f"; from: {result.account_address}" if result.account_address else ""
        attachment = (
            f"\nAttachments: {', '.join(item.filename for item in request.attachments)}"
            if request.attachments
            else ""
        )
        warnings = (
            f"\nWarnings: {'; '.join(result.warnings)}" if result.warnings else ""
        )
        return Str(
            value=(
                "[success] Created email draft"
                f"\nDraft id: {result.draft_id}"
                f"\nTo: {recipients}{cc}{account}"
                f"\nSubject: {request.subject}"
                f"{attachment}"
                f"\nThis draft has not been sent.{warnings}"
            )
        )
    except ValueError as exc:
        return Str(value=f"[error] Invalid email draft: {exc}")
    except EmailProviderError as exc:
        return Str(value=f"[error] Unable to create email draft: {exc}")
    except ExternalCallTimeoutError:
        return Str(
            value="[error] Email draft creation timed out; check the Drafts folder before retrying."
        )
    except (ExternalCallCancelledError, ExternalCallInterruptedError):
        return Str(value="[error] Email draft creation was cancelled.")
    except Exception:  # noqa: BLE001
        return Str(
            value="[error] Unable to create email draft due to an email provider failure."
        )


@factory
def execute_reply_draft(
    input: GuardFilesResult,
    messages: list[Message],
    ctx: EmailRuntimeContext,
) -> Str:
    del messages
    try:
        original = input.original_input
        if not isinstance(original, CreateReplyDraft):
            raise EmailProviderError("unsupported email reply operation")
        request = resolve_reply_draft_request(
            original, attachments=_attachments_from_guard(input)
        )
        result = run_email_call(
            ctx,
            label="email-create-reply-draft",
            cancelled_message="Email reply draft creation was cancelled",
            operation=lambda: ctx.service.resource.create_reply_draft(
                request, is_cancelled=ctx.is_cancelled
            ),
        )
        account = f"; from: {result.account_address}" if result.account_address else ""
        attachment = (
            f"\nAttachments: {', '.join(item.filename for item in request.attachments)}"
            if request.attachments
            else ""
        )
        quoted_original = (
            "\nQuoted original: included"
            if result.quoted_original_included
            else "\nQuoted original: not included"
        )
        cc = f"\nCc: {', '.join(result.cc)}" if result.cc else ""
        warnings = (
            f"\nWarnings: {'; '.join(result.warnings)}" if result.warnings else ""
        )
        return Str(
            value=(
                "[success] Created email reply draft"
                f"\nDraft id: {result.draft_id}"
                f"\nTo: {', '.join(result.to)}{account}"
                f"{cc}"
                f"\nSubject: {result.subject}"
                f"{attachment}"
                f"{quoted_original}"
                f"\nThis draft has not been sent.{warnings}"
            )
        )
    except ValueError as exc:
        return Str(value=f"[error] Invalid email reply draft: {exc}")
    except EmailProviderError as exc:
        return Str(value=f"[error] Unable to create email reply draft: {exc}")
    except ExternalCallTimeoutError:
        return Str(
            value="[error] Email reply draft creation timed out; check the Drafts folder before retrying."
        )
    except (ExternalCallCancelledError, ExternalCallInterruptedError):
        return Str(value="[error] Email reply draft creation was cancelled.")
    except Exception:  # noqa: BLE001
        return Str(
            value="[error] Unable to create email reply draft due to an email provider failure."
        )
