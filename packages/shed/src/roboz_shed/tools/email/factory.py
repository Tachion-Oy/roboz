"""Factories for provider-neutral email search and draft tools."""

from collections.abc import Callable
from pathlib import Path

from roboz.runtime.pipe import EventPipe
from roboz.tooling import ToolDependency
from roboz.tooling import Tool

from roboz_shed.identifiers import (
    CREATE_EMAIL_DRAFT_TOOL_NAME,
    CREATE_REPLY_DRAFT_TOOL_NAME,
    DOWNLOAD_EMAIL_ATTACHMENT_TOOL_NAME,
    EMAIL_TOOLS_SKILL_NAME,
    READ_EMAIL_TOOL_NAME,
    SEARCH_EMAIL_TOOL_NAME,
)
from roboz_shed.models import (
    ActionVerdict,
    GuardCtx,
    PermissionRule,
)
from roboz_shed.tools.email.contracts import EmailService
from roboz_shed.tools.email.drafts import (
    execute_email_operation,
    execute_reply_draft,
    resolve_attachment_download,
    resolve_email_input,
    resolve_reply_draft_input,
)
from roboz_shed.tools.email.messages import (
    execute_attachment_download,
    read_email,
    search_email,
)
from roboz_shed.tools.email.runtime import EmailRuntimeContext
from roboz_shed.tools.guard import build_guarded_tool_chain
from roboz_shed.tools.types import EmailToolCtx
from roboz_shed.tools.utils import resolve_tool_base

DEFAULT_EMAIL_OPERATION_TIMEOUT_S = 30.0


def get_work_with_email(
    *,
    service: EmailService,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None = None,
    allow_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    takes_precedence: ActionVerdict | None = None,
    is_cancelled: Callable[[], bool] = lambda: False,
    pipe: EventPipe | None = None,
    timeout_s: float = DEFAULT_EMAIL_OPERATION_TIMEOUT_S,
    email_skill_name: str = EMAIL_TOOLS_SKILL_NAME,
    prompt_before_inbox_read: bool = False,
) -> list[Tool]:
    """Build standalone draft, received-email search, and reply-draft tools.

    Inbox reads proceed directly unless ``prompt_before_inbox_read`` is enabled.
    """

    runtime = EmailRuntimeContext(
        service=ToolDependency(service),
        is_cancelled=is_cancelled,
        timeout_s=timeout_s,
        pipe=pipe,
        prompt_before_inbox_read=prompt_before_inbox_read,
    )
    search_description = (
        "Search Inbox, Drafts, or Sent and return newest-first bounded metadata, "
        "160-character previews, and opaque source references. Input requires "
        "mailbox and supports from_address?, to_address?, subject_contains?, "
        "text_contains?, since?, before?, and limit? (1-20, default 10). "
        f"Load `{email_skill_name}` for privacy and source-safety guidance."
    )
    return [
        *_get_create_email_draft_tools(
            service=service,
            base=base,
            default_verdict=default_verdict,
            deny_rules=deny_rules,
            allow_rules=allow_rules,
            ask_rules=ask_rules,
            takes_precedence=takes_precedence,
            is_cancelled=is_cancelled,
            pipe=pipe,
            timeout_s=timeout_s,
            email_skill_name=email_skill_name,
        ),
        search_email(runtime).copy(
            name=SEARCH_EMAIL_TOOL_NAME,
            description=search_description,
        ),
        read_email(runtime).copy(
            name=READ_EMAIL_TOOL_NAME,
            description=(
                "Open one bounded message from Inbox, Drafts, or Sent. Successful "
                "Inbox reads mark the message read. Drafts and Sent are user-authored "
                "and read-only."
                + (
                    " Inbox reads prompt before fetching."
                    if prompt_before_inbox_read
                    else ""
                )
            ),
        ),
        *_get_download_attachment_tools(
            ctx=runtime,
            base=base,
            default_verdict=default_verdict,
            deny_rules=deny_rules,
            allow_rules=allow_rules,
            ask_rules=ask_rules,
            takes_precedence=takes_precedence,
            pipe=pipe,
        ),
        *_get_create_reply_draft_tools(
            ctx=runtime,
            base=base,
            default_verdict=default_verdict,
            deny_rules=deny_rules,
            allow_rules=allow_rules,
            ask_rules=ask_rules,
            takes_precedence=takes_precedence,
            pipe=pipe,
            email_skill_name=email_skill_name,
        ),
    ]


def _get_download_attachment_tools(
    *,
    ctx: EmailRuntimeContext,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None,
    allow_rules: list[PermissionRule] | None,
    ask_rules: list[PermissionRule] | None,
    takes_precedence: ActionVerdict | None,
    pipe: EventPipe | None,
) -> list[Tool]:
    resolved_base = resolve_tool_base(base)
    guard_ctx = GuardCtx(
        base=resolved_base,
        takes_precedence=takes_precedence or ActionVerdict.deny,
        default_verdict=default_verdict,
        allow=list(allow_rules or []),
        deny=list(deny_rules or []),
        ask=list(ask_rules or []),
        pipe=pipe,
    )
    entry = resolve_attachment_download(EmailToolCtx(base=resolved_base)).copy(
        name=DOWNLOAD_EMAIL_ATTACHMENT_TOOL_NAME,
        description=(
            "Download one email attachment to a specific local path. "
            "Input is `{attachment_ref, destination_path}`. Relative paths use "
            "the configured tool base; absolute paths remain absolute. The "
            "destination is protected by filesystem CREATE/overwrite permissions."
        ),
    )
    return build_guarded_tool_chain(
        entry=entry,
        guard_ctx=guard_ctx,
        execute=execute_attachment_download(ctx),
    )


def _get_create_email_draft_tools(
    *,
    service: EmailService,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None = None,
    allow_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    takes_precedence: ActionVerdict | None = None,
    is_cancelled: Callable[[], bool] = lambda: False,
    pipe: EventPipe | None = None,
    timeout_s: float = DEFAULT_EMAIL_OPERATION_TIMEOUT_S,
    email_skill_name: str = EMAIL_TOOLS_SKILL_NAME,
) -> list[Tool]:
    """Create the draft-only email tool chain with path guards for attachments.

    Sending is deliberately absent: the service can only persist a draft and
    the public input model exposes only ``create_draft``. Optional
    ``attachment_paths`` are resolved like other structured file tools and gated
    by READ permission only.
    """

    allow = list(allow_rules if allow_rules else [])
    deny = list(deny_rules if deny_rules else [])
    ask = list(ask_rules if ask_rules else [])
    precedence = takes_precedence if takes_precedence else ActionVerdict.deny
    resolved_base = resolve_tool_base(base)

    email_ctx = EmailToolCtx(base=resolved_base)
    guard_ctx = GuardCtx(
        base=resolved_base,
        takes_precedence=precedence,
        default_verdict=default_verdict,
        allow=allow,
        deny=deny,
        ask=ask,
        pipe=pipe,
    )

    description = (
        "Create a real server-side email draft using semantic fields. This tool "
        "can only create drafts; it cannot send, read, search, delete, or alter "
        "mailbox state otherwise. Input is `{to, cc, bcc, subject, body_text, "
        "from_address?, reply_to?, client_request_id?, "
        "attachment_paths?}`. Optional `attachment_paths` is a list of file paths "
        "(relative to the tool base or absolute; no globs; max 10). Each path "
        "requires READ permission. "
        f"The possibly available `{email_skill_name}` skill has usage guidance."
    )
    execute_ctx = EmailRuntimeContext(
        service=ToolDependency(service),
        is_cancelled=is_cancelled,
        timeout_s=timeout_s,
        pipe=pipe,
    )
    entry = resolve_email_input(email_ctx).copy(
        name=CREATE_EMAIL_DRAFT_TOOL_NAME, description=description
    )
    return build_guarded_tool_chain(
        entry=entry,
        guard_ctx=guard_ctx,
        execute=execute_email_operation(execute_ctx),
    )


def _get_create_reply_draft_tools(
    *,
    ctx: EmailRuntimeContext,
    base: Path,
    default_verdict: ActionVerdict,
    deny_rules: list[PermissionRule] | None,
    allow_rules: list[PermissionRule] | None,
    ask_rules: list[PermissionRule] | None,
    takes_precedence: ActionVerdict | None,
    pipe: EventPipe | None,
    email_skill_name: str,
) -> list[Tool]:
    resolved_base = resolve_tool_base(base)
    guard_ctx = GuardCtx(
        base=resolved_base,
        takes_precedence=takes_precedence or ActionVerdict.deny,
        default_verdict=default_verdict,
        allow=list(allow_rules or []),
        deny=list(deny_rules or []),
        ask=list(ask_rules or []),
        pipe=pipe,
    )
    description = (
        "Create a real server-side reply draft from a source_message_ref returned "
        f"by `{SEARCH_EMAIL_TOOL_NAME}` from Inbox. To/Cc recipients, subject, and thread "
        "headers are derived from the source and cannot be supplied by the caller. "
        "The operation replies to all visible source recipients by default, excluding "
        "the configured sender; set `reply_all` to false for a sender-only reply. "
        "The source message is quoted beneath the reply by default; set "
        "`include_quoted_original` to false to omit it. Input is "
        "`{source_message_ref, body_text, include_quoted_original?, reply_all?, "
        "from_address?, client_request_id?, attachment_paths?}`. The draft is not sent. "
        f"Load `{email_skill_name}` for usage guidance."
    )
    entry = resolve_reply_draft_input(EmailToolCtx(base=resolved_base)).copy(
        name=CREATE_REPLY_DRAFT_TOOL_NAME,
        description=description,
    )
    return build_guarded_tool_chain(
        entry=entry,
        guard_ctx=guard_ctx,
        execute=execute_reply_draft(ctx),
    )
