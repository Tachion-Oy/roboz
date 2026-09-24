"""Factories for provider-neutral email search and draft tools."""

from collections.abc import Callable
from pathlib import Path

from roboz.shed.identifiers import (
    CREATE_EMAIL_DRAFT_TOOL_NAME,
    CREATE_REPLY_DRAFT_TOOL_NAME,
    DOWNLOAD_EMAIL_ATTACHMENT_TOOL_NAME,
    EMAIL_TOOLS_SKILL_NAME,
    READ_EMAIL_TOOL_NAME,
    SEARCH_EMAIL_TOOL_NAME,
)
from roboz.shed.models import ActionVerdict, PermissionRule
from roboz.shed.tools.email.contracts import EmailService
from roboz.shed.tools.email.drafts import (
    execute_email_operation,
    execute_reply_draft,
    resolve_attachment_download,
    resolve_email_input,
    resolve_reply_draft_input,
)
from roboz.shed.tools.email.messages import (
    execute_attachment_download,
    read_email,
    search_email,
)
from roboz.shed.tools.guard import build_guarded_tool_chain
from roboz.shed.tools.utils import resolve_tool_base
from roboz.shed.tools.contexts import EmailContext, GuardContext
from roboz.runtime.pipe import EventPipe
from roboz.tooling import Tool

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
    if not isinstance(service, EmailService):
        raise TypeError("service must implement EmailService")
    runtime = EmailContext(
        service=service,
        is_cancelled=is_cancelled,
        timeout_s=timeout_s,
        pipe=pipe,
        prompt_before_inbox_read=prompt_before_inbox_read,
    )
    resolved_base = resolve_tool_base(base)
    guard_ctx = GuardContext(
        base=resolved_base,
        default_verdict=default_verdict,
        takes_precedence=takes_precedence or ActionVerdict.deny,
        allow=list(allow_rules or []),
        deny=list(deny_rules or []),
        ask=list(ask_rules or []),
        pipe=pipe,
    )
    draft = resolve_email_input(resolved_base).copy(
        name=CREATE_EMAIL_DRAFT_TOOL_NAME,
        description=(
            "Create a server-side draft without sending it. Attach local files with "
            "attachment_paths (relative to the tool base or absolute; no globs; max 10). "
            "Every attachment requires READ permission. "
            f"Load `{email_skill_name}` for usage guidance."
        ),
    )
    download = resolve_attachment_download(resolved_base).copy(
        name=DOWNLOAD_EMAIL_ATTACHMENT_TOOL_NAME,
        description=(
            "Download one email attachment to destination_path. Relative paths use "
            "the tool base; absolute paths remain absolute. The destination requires "
            "filesystem CREATE/overwrite permission."
        ),
    )
    reply = resolve_reply_draft_input(resolved_base).copy(
        name=CREATE_REPLY_DRAFT_TOOL_NAME,
        description=(
            "Create a reply draft from an Inbox source_message_ref. Recipients, subject, "
            "and threading come from the source. Reply to all visible recipients except "
            "the configured sender by default; set reply_all=false for a sender-only reply. "
            "Set include_quoted_original=false to omit the original body. Every local "
            "attachment requires READ permission. The draft is not sent. "
            f"Load `{email_skill_name}` for usage guidance."
        ),
    )
    return [
        *build_guarded_tool_chain(
            entry=draft, guard_ctx=guard_ctx, execute=execute_email_operation(runtime)
        ),
        search_email(runtime).copy(
            name=SEARCH_EMAIL_TOOL_NAME,
            description=(
                "Search Inbox, Drafts, or Sent with structured filters. Return newest-first "
                "metadata, 160-character previews, and opaque source references. "
                f"Load `{email_skill_name}` for privacy and source-safety guidance."
            ),
        ),
        read_email(runtime).copy(
            name=READ_EMAIL_TOOL_NAME,
            description=(
                "Open one bounded message from Inbox, Drafts, or Sent. Successful Inbox "
                "reads mark the message read. Drafts and Sent remain read-only."
                + (
                    " Inbox reads prompt before fetching."
                    if prompt_before_inbox_read
                    else ""
                )
            ),
        ),
        *build_guarded_tool_chain(
            entry=download,
            guard_ctx=guard_ctx,
            execute=execute_attachment_download(runtime),
        ),
        *build_guarded_tool_chain(
            entry=reply, guard_ctx=guard_ctx, execute=execute_reply_draft(runtime)
        ),
    ]
