"""Email factories require concrete contexts with the complete service contract."""

from pathlib import Path
from typing import assert_type

from pydantic import SecretStr

from roboz.shed.models import GuardFilesResult, ParseError
from roboz.shed.tools.contexts import EmailContext
from roboz.shed.tools.email import EmailService, get_work_with_email
from roboz.shed.tools.email.proton_bridge import (
    ProtonBridgeEmailService,
    ProtonBridgeSettings,
    ProtonBridgeTlsMode,
)
from roboz.shed.tools.email.drafts import (
    execute_email_operation,
    execute_reply_draft,
    resolve_attachment_download,
    resolve_email_input,
    resolve_reply_draft_input,
)
from roboz.shed.tools.email.inputs import (
    CreateEmailDraft,
    CreateReplyDraft,
    DownloadEmailAttachment,
    ReadEmail,
    SearchEmail,
)
from roboz.shed.tools.email.messages import (
    execute_attachment_download,
    read_email,
    search_email,
)
from roboz.shed.tools.types import ResolvedFileCommand
from roboz.shed.models import ActionVerdict
from roboz import Factory, Tool
from roboz.models import Str
from roboz.dependencies import ExternalDependency

assert_type(search_email, Factory[SearchEmail, Str, EmailContext])
assert_type(read_email, Factory[ReadEmail, Str, EmailContext])
assert_type(execute_email_operation, Factory[GuardFilesResult, Str, EmailContext])
assert_type(execute_reply_draft, Factory[GuardFilesResult, Str, EmailContext])
assert_type(execute_attachment_download, Factory[GuardFilesResult, Str, EmailContext])
assert_type(
    resolve_email_input,
    Factory[CreateEmailDraft, ResolvedFileCommand | ParseError, Path],
)
assert_type(
    resolve_reply_draft_input,
    Factory[CreateReplyDraft, ResolvedFileCommand | ParseError, Path],
)
assert_type(
    resolve_attachment_download,
    Factory[DownloadEmailAttachment, ResolvedFileCommand | ParseError, Path],
)


def bind(service: EmailService, base: Path) -> None:
    context = EmailContext(
        service=service, is_cancelled=lambda: False, timeout_s=30, pipe=None
    )
    assert_type(context.service, EmailService)
    assert_type(context.service.check(), bool)
    assert_type(context.external_dependencies(), tuple[ExternalDependency, ...])
    assert_type(search_email(context), Tool[SearchEmail, Str])
    assert_type(
        get_work_with_email(
            service=service, base=base, default_verdict=ActionVerdict.deny
        ),
        list[Tool],
    )


def bind_proton_bridge(base: Path, username: str, password: str) -> None:
    settings = ProtonBridgeSettings(
        imap_host="127.0.0.1",
        imap_port=1143,
        tls_mode=ProtonBridgeTlsMode.STARTTLS,
        account_address="me@example.com",
        username=SecretStr(username),
        password=SecretStr(password),
    )
    service = ProtonBridgeEmailService(settings)
    assert_type(service, ProtonBridgeEmailService)
    assert_type(
        get_work_with_email(
            service=service, base=base, default_verdict=ActionVerdict.deny
        ),
        list[Tool],
    )
