import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from roboz.agent import AgentMode
from roboz.shed.identifiers import (
    DOWNLOAD_EMAIL_ATTACHMENT_TOOL_NAME,
    READ_EMAIL_TOOL_NAME,
)
from roboz.shed.models import (
    ActionVerdict,
    GuardFilesResult,
    GuardStatus,
    Operation,
    ParseError,
    PermissionRule,
)
from roboz.shed.skills.email_tools.prompts import INSTRUCTIONS as EMAIL_INSTRUCTIONS
from roboz.shed.tools.email import (
    DownloadedEmailAttachment,
    EmailDraftAttachment,
    EmailDraftRequest,
    EmailDraftResult,
    EmailMailbox,
    EmailMessage,
    EmailProviderError,
    EmailReplyDraftRequest,
    EmailReplyDraftResult,
    EmailSearchRequest,
    EmailService,
    EmailSummary,
)
from roboz.shed.tools.email import messages as email_messages
from roboz.shed.tools.email import runtime as email_runtime
from roboz.shed.tools.email.factory import get_work_with_email
from roboz.shed.tools.email.inputs import (
    CreateEmailDraft,
    CreateReplyDraft,
    DownloadEmailAttachment,
    ReadEmail,
    SearchEmail,
)
from roboz.shed.tools.types import ResolvedFileCommand

from roboz import Agent
from roboz.tools import stop
from roboz.dependencies import ExecutableDependency, ExternalDependencyKind
from roboz.shed.dependency_health import DependencyHealthMonitor, DependencyStatus

from roboz.exceptions import (
    ExternalCallCancelledError,
    ExternalCallTimeoutError,
)
from roboz.runtime.pipe import EventPipe


class _FakeDraftService(EmailService):
    def __init__(self) -> None:
        self.requests: list[EmailDraftRequest] = []

    def create_draft(
        self, request: EmailDraftRequest, *, is_cancelled: Callable[[], bool]
    ) -> EmailDraftResult:
        assert not is_cancelled()
        self.requests.append(request)
        return EmailDraftResult(
            draft_id="provider-draft-1", account_address="me@example.com"
        )

    @property
    def dependency_id(self) -> str:
        return "network:test_email"

    def redacted_metadata(self) -> dict[str, str]:
        return {"provider": "test"}

    def probe(self) -> dict[str, object]:
        return {"available": True}

    def search_messages(self, request, *, is_cancelled):
        raise NotImplementedError

    def read_message(self, mailbox, source_message_ref, *, is_cancelled):
        raise NotImplementedError

    def download_attachment(self, attachment_ref, *, is_cancelled):
        raise NotImplementedError

    def create_reply_draft(self, request, *, is_cancelled):
        raise NotImplementedError


class _FakeMailboxService(EmailService):
    def __init__(self) -> None:
        self.draft_requests: list[EmailDraftRequest] = []
        self.search_requests: list[EmailSearchRequest] = []
        self.reply_requests: list[EmailReplyDraftRequest] = []
        self.read_requests: list[str] = []
        self.read_mailboxes: list[EmailMailbox] = []
        self.download_requests: list[str] = []

    def create_draft(
        self, request: EmailDraftRequest, *, is_cancelled: Callable[[], bool]
    ) -> EmailDraftResult:
        assert not is_cancelled()
        self.draft_requests.append(request)
        return EmailDraftResult(
            draft_id="provider-draft-1", account_address="me@example.com"
        )

    def search_messages(
        self, request: EmailSearchRequest, *, is_cancelled: Callable[[], bool]
    ) -> tuple[EmailSummary, ...]:
        assert not is_cancelled()
        self.search_requests.append(request)
        return (
            EmailSummary(
                source_message_ref="proton-imap-message:v1:test",
                mailbox=request.mailbox,
                sender="Alice <alice@example.com>",
                to=("me@example.com",),
                subject="Project update",
                timestamp=datetime(2026, 7, 24, 8, 0, tzinfo=UTC),
                replyable=request.mailbox is EmailMailbox.INBOX,
            ),
        )

    def create_reply_draft(
        self,
        request: EmailReplyDraftRequest,
        *,
        is_cancelled: Callable[[], bool],
    ) -> EmailReplyDraftResult:
        assert not is_cancelled()
        self.reply_requests.append(request)
        return EmailReplyDraftResult(
            draft_id="provider-reply-1",
            account_address="me@example.com",
            to=("alice@example.com",),
            subject="Re: Project update",
            cc=("copy@example.com",),
            quoted_original_included=request.include_quoted_original,
        )

    def read_message(
        self,
        mailbox: EmailMailbox,
        source_message_ref: str,
        *,
        is_cancelled: Callable[[], bool],
    ) -> EmailMessage:
        assert not is_cancelled()
        self.read_requests.append(source_message_ref)
        self.read_mailboxes.append(mailbox)
        return EmailMessage(
            source_message_ref=source_message_ref,
            mailbox=mailbox,
            sender="Alice <alice@example.com>",
            to=("me@example.com",),
            cc=(),
            bcc=(),
            subject="Project update",
            timestamp=datetime(2026, 7, 24, 8, 0, tzinfo=UTC),
            body_text="Message body",
        )

    def download_attachment(
        self,
        attachment_ref: str,
        *,
        is_cancelled: Callable[[], bool],
    ) -> DownloadedEmailAttachment:
        assert attachment_ref
        assert not is_cancelled()
        self.download_requests.append(attachment_ref)
        return DownloadedEmailAttachment(
            filename="notes.txt",
            content_type="text/plain",
            data=b"notes",
        )

    @property
    def dependency_id(self) -> str:
        return "network:test_email"

    def redacted_metadata(self) -> dict[str, str]:
        return {"provider": "test"}

    def probe(self) -> dict[str, object]:
        return {"available": True}


def _input(**overrides: object) -> CreateEmailDraft:
    fields: dict[str, object] = {
        "to": [" Person@example.com ", "person@example.com"],
        "cc": ["team@example.com"],
        "bcc": ["private@example.com"],
        "subject": "Hello",
        "body_text": "Hello from Agent.",
    }
    fields.update(overrides)
    return CreateEmailDraft.model_validate(fields)


def _tools(
    tmp_path: Path,
    provider: EmailService,
    *,
    default_verdict: ActionVerdict = ActionVerdict.allow,
    allow_rules: list[PermissionRule] | None = None,
    deny_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    pipe: EventPipe | None = None,
):
    return get_work_with_email(
        service=provider,
        base=tmp_path,
        default_verdict=default_verdict,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
        ask_rules=ask_rules,
        takes_precedence=ActionVerdict.allow,
        pipe=pipe,
    )


def _mailbox_tools(
    tmp_path: Path,
    provider: EmailService,
    *,
    pipe: EventPipe | None = None,
    prompt_before_inbox_read: bool = False,
):
    return get_work_with_email(
        service=provider,
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        takes_precedence=ActionVerdict.allow,
        pipe=pipe,
        prompt_before_inbox_read=prompt_before_inbox_read,
    )


def _run_chain(tools, payload):
    entry, guard, execute = tools[:3]
    resolved = entry(payload, messages=[])
    if isinstance(resolved, ParseError):
        return resolved, None, None
    assert isinstance(resolved, ResolvedFileCommand)
    guarded = guard(resolved, messages=[])
    assert isinstance(guarded, GuardFilesResult)
    if guarded.status != GuardStatus.ALLOWED:
        return resolved, guarded, None
    return resolved, guarded, execute(guarded, messages=[])


def test_work_with_email_creates_normalized_draft_without_sending(
    tmp_path: Path,
) -> None:
    provider = _FakeDraftService()
    tools = _tools(tmp_path, provider)

    _, _, result = _run_chain(tools, _input())

    assert result is not None
    assert provider.requests == [
        EmailDraftRequest(
            to=("Person@example.com",),
            cc=("team@example.com",),
            bcc=("private@example.com",),
            subject="Hello",
            body_text="Hello from Agent.",
            from_address=None,
            reply_to=None,
            client_request_id=None,
            attachments=(),
        )
    ]
    assert "provider-draft-1" in result.value
    assert "not been sent" in result.value
    assert "private@example.com" not in result.value


def test_work_with_email_attaches_relative_path_under_base(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "notes.pdf"
    source.write_bytes(b"%PDF-1.4")
    provider = _FakeDraftService()
    tools = _tools(tmp_path, provider)

    _, _, result = _run_chain(tools, _input(attachment_paths=["docs/notes.pdf"]))

    assert result is not None
    assert provider.requests[0].attachments == (
        EmailDraftAttachment(path=str(source.resolve()), filename="notes.pdf"),
    )
    assert "Attachments: notes.pdf" in result.value
    assert str(source.resolve()) not in result.value


def test_work_with_email_attaches_multiple_paths(tmp_path: Path) -> None:
    pdf = tmp_path / "CV.pdf"
    tex = tmp_path / "CV.tex"
    pdf.write_bytes(b"%PDF")
    tex.write_text("tex", encoding="utf-8")
    provider = _FakeDraftService()
    tools = _tools(tmp_path, provider)

    _, guarded, result = _run_chain(
        tools, _input(attachment_paths=["CV.pdf", "CV.tex", "CV.pdf"])
    )

    assert guarded is not None
    assert len(guarded.items) == 2
    assert result is not None
    assert provider.requests[0].attachments == (
        EmailDraftAttachment(path=str(pdf.resolve()), filename="CV.pdf"),
        EmailDraftAttachment(path=str(tex.resolve()), filename="CV.tex"),
    )
    assert "Attachments: CV.pdf, CV.tex" in result.value


def test_work_with_email_attaches_absolute_path(tmp_path: Path) -> None:
    source = tmp_path / "report.docx"
    source.write_bytes(b"docx")
    provider = _FakeDraftService()
    tools = _tools(tmp_path, provider)

    _, _, result = _run_chain(tools, _input(attachment_paths=[str(source)]))

    assert result is not None
    assert provider.requests[0].attachments == (
        EmailDraftAttachment(path=str(source.resolve()), filename="report.docx"),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"subject": "safe\r\nBcc: injected@example.com"},
        {"to": ["bad\r\nBcc: injected@example.com"]},
        {"body_text": " \n"},
    ],
)
def test_work_with_email_rejects_unsafe_or_empty_content(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    provider = _FakeDraftService()
    tools = _tools(tmp_path, provider)

    resolved, _, result = _run_chain(tools, _input(**overrides))

    assert isinstance(resolved, ParseError)
    assert "Invalid email draft" in resolved.message
    assert result is None
    assert provider.requests == []


def test_work_with_email_rejects_glob_attachment_path(tmp_path: Path) -> None:
    provider = _FakeDraftService()
    tools = _tools(tmp_path, provider)

    resolved, _, _ = _run_chain(tools, _input(attachment_paths=["docs/*.pdf"]))

    assert isinstance(resolved, ParseError)
    assert "must name one file, not a glob" in resolved.message
    assert provider.requests == []


def test_work_with_email_rejects_missing_or_directory_attachment(
    tmp_path: Path,
) -> None:
    provider = _FakeDraftService()
    tools = _tools(tmp_path, provider)
    missing = _run_chain(tools, _input(attachment_paths=["missing.pdf"]))
    assert isinstance(missing[0], ParseError)
    assert "existing regular file" in missing[0].message

    folder = tmp_path / "folder"
    folder.mkdir()
    as_dir = _run_chain(tools, _input(attachment_paths=["folder"]))
    assert isinstance(as_dir[0], ParseError)
    assert "existing regular file" in as_dir[0].message
    assert provider.requests == []


def test_work_with_email_denies_attachment_without_read_permission(
    tmp_path: Path,
) -> None:
    source = tmp_path / "secret.pdf"
    source.write_bytes(b"x")
    provider = _FakeDraftService()
    tools = _tools(
        tmp_path,
        provider,
        default_verdict=ActionVerdict.deny,
        allow_rules=[],
        deny_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
    )

    _, guarded, result = _run_chain(tools, _input(attachment_paths=["secret.pdf"]))

    assert guarded is not None
    assert guarded.status == GuardStatus.DENIED
    assert result is None
    assert provider.requests == []


def test_work_with_email_allows_attachment_with_read_even_if_create_denied(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ok.pdf"
    source.write_bytes(b"x")
    provider = _FakeDraftService()
    tools = _tools(
        tmp_path,
        provider,
        default_verdict=ActionVerdict.deny,
        allow_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        deny_rules=[PermissionRule(pattern="**", operations={Operation.CREATE})],
    )

    _, guarded, result = _run_chain(tools, _input(attachment_paths=["ok.pdf"]))

    assert guarded is not None
    assert guarded.status == GuardStatus.ALLOWED
    assert result is not None
    assert provider.requests[0].attachments
    assert guarded.items[0].value.kind == "email_ready"


def test_work_with_email_ask_rule_can_block_attachment_read(tmp_path: Path) -> None:
    source = tmp_path / "ask-me.pdf"
    source.write_bytes(b"x")
    provider = _FakeDraftService()
    pipe = EventPipe()
    tools = _tools(
        tmp_path,
        provider,
        default_verdict=ActionVerdict.allow,
        ask_rules=[PermissionRule(pattern="**", operations={Operation.READ})],
        pipe=pipe,
    )

    with patch("roboz.shed.tools.utils.interact_with_user", return_value="no"):
        _, guarded, result = _run_chain(tools, _input(attachment_paths=["ask-me.pdf"]))

    assert guarded is not None
    assert guarded.status == GuardStatus.DENIED
    assert result is None
    assert provider.requests == []


def test_work_with_email_schema_rejects_more_than_ten_attachments() -> None:
    with pytest.raises(ValidationError):
        _input(attachment_paths=[f"f{i}.pdf" for i in range(11)])


def test_work_with_email_schema_requires_a_primary_recipient() -> None:
    with pytest.raises(ValidationError):
        _input(to=[])


def test_work_with_email_schema_has_no_send_operation() -> None:
    with pytest.raises(ValidationError):
        CreateEmailDraft.model_validate(
            {
                "operation": "send",
                "to": ["person@example.com"],
                "subject": "No",
                "body_text": "No",
            }
        )


def test_search_email_requires_mailbox() -> None:
    with pytest.raises(ValidationError):
        SearchEmail.model_validate({"limit": 1})


def test_work_with_email_redacts_unexpected_service_errors(tmp_path: Path) -> None:
    class _FailingService(_FakeDraftService):
        def create_draft(
            self, request: EmailDraftRequest, *, is_cancelled: Callable[[], bool]
        ) -> EmailDraftResult:
            del request, is_cancelled
            raise RuntimeError("secret bridge password")

    tools = _tools(tmp_path, _FailingService())

    _, _, result = _run_chain(tools, _input())

    assert result is not None
    assert "secret bridge password" not in result.value
    assert result.value.startswith("[error]")


def test_work_with_email_returns_safe_provider_error(tmp_path: Path) -> None:
    class _FailingService(_FakeDraftService):
        def create_draft(
            self, request: EmailDraftRequest, *, is_cancelled: Callable[[], bool]
        ) -> EmailDraftResult:
            del request, is_cancelled
            raise EmailProviderError("Bridge is unavailable")

    tools = _tools(tmp_path, _FailingService())
    _, _, result = _run_chain(tools, _input())
    assert result is not None
    assert "Bridge is unavailable" in result.value


def test_work_with_email_reports_ambiguous_timeout(tmp_path: Path, monkeypatch) -> None:
    def _timeout(*args, **kwargs):
        del args, kwargs
        raise ExternalCallTimeoutError("timed out")

    monkeypatch.setattr(email_runtime, "run_cancellable_external_call", _timeout)
    tools = _tools(tmp_path, _FakeDraftService())

    _, _, result = _run_chain(tools, _input())

    assert result is not None
    assert "check the Drafts folder before retrying" in result.value


def test_work_with_email_passes_runtime_control_signals(
    tmp_path: Path, monkeypatch
) -> None:
    pipe = EventPipe()
    captured: dict[str, object] = {}

    def _cancel(*args, **kwargs):
        del args
        captured.update(kwargs)
        raise ExternalCallCancelledError("cancelled")

    monkeypatch.setattr(email_runtime, "run_cancellable_external_call", _cancel)
    tools = _tools(tmp_path, _FakeDraftService(), pipe=pipe)

    _, _, result = _run_chain(tools, _input())

    assert result is not None
    assert captured["control_signals"] == pipe.control_signals
    assert "was cancelled" in result.value


def test_search_email_returns_bounded_untrusted_metadata(
    tmp_path: Path,
) -> None:
    provider = _FakeMailboxService()
    tools = _mailbox_tools(tmp_path, provider)
    search = tools[3]

    result = search(
        SearchEmail(
            mailbox="inbox",
            from_address=" alice@example.com ",
            subject_contains=" Project ",
            limit=5,
        ),
        messages=[],
    )

    assert provider.search_requests == [
        EmailSearchRequest(
            mailbox=EmailMailbox.INBOX,
            from_address="alice@example.com",
            subject_contains="Project",
            limit=5,
        )
    ]
    assert "untrusted mailbox data" in result.value
    assert "newest first" in result.value
    assert "proton-imap-message:v1:test" in result.value
    assert "Project update" in result.value
    assert "Status: unread" in result.value
    assert "Preview (untrusted" in result.value


def test_read_email_can_prompt_before_provider(
    tmp_path: Path,
) -> None:
    provider = _FakeMailboxService()
    pipe = EventPipe()
    denied_tools = _mailbox_tools(
        tmp_path,
        provider,
        pipe=pipe,
        prompt_before_inbox_read=True,
    )
    denied_tool = next(
        tool for tool in denied_tools if tool.name == READ_EMAIL_TOOL_NAME
    )

    with patch.object(email_messages, "interact_with_user", return_value="no"):
        denied = denied_tool(
            ReadEmail(
                mailbox="inbox",
                source_message_ref="proton-imap-message:v1:test",
            ),
            messages=[],
        )

    assert "not authorized" in denied.value
    assert provider.read_requests == []

    allowed_tools = _mailbox_tools(
        tmp_path,
        provider,
        pipe=pipe,
        prompt_before_inbox_read=True,
    )
    allowed_tool = next(
        tool for tool in allowed_tools if tool.name == READ_EMAIL_TOOL_NAME
    )
    with patch.object(email_messages, "interact_with_user", return_value="yes"):
        allowed = allowed_tool(
            ReadEmail(
                mailbox="inbox",
                source_message_ref="proton-imap-message:v1:test",
            ),
            messages=[],
        )

    assert provider.read_requests == ["proton-imap-message:v1:test"]
    assert "BEGIN UNTRUSTED EMAIL BODY" in allowed.value
    assert "Message body" in allowed.value


def test_read_email_fails_closed_without_interaction(tmp_path: Path) -> None:
    provider = _FakeMailboxService()
    tool = next(
        tool
        for tool in _mailbox_tools(
            tmp_path,
            provider,
            prompt_before_inbox_read=True,
        )
        if tool.name == READ_EMAIL_TOOL_NAME
    )

    result = tool(ReadEmail(mailbox="inbox", source_message_ref="source"), messages=[])

    assert "not authorized" in result.value
    assert provider.read_requests == []


def test_read_email_does_not_prompt_by_default(tmp_path: Path) -> None:
    provider = _FakeMailboxService()
    tool = next(
        tool
        for tool in _mailbox_tools(tmp_path, provider)
        if tool.name == READ_EMAIL_TOOL_NAME
    )

    with patch.object(email_messages, "interact_with_user") as prompt:
        result = tool(
            ReadEmail(mailbox="inbox", source_message_ref="source"),
            messages=[],
        )

    prompt.assert_not_called()
    assert provider.read_requests == ["source"]
    assert "BEGIN UNTRUSTED EMAIL BODY" in result.value


@pytest.mark.parametrize("mailbox", ["drafts", "sent"])
def test_user_authored_email_reads_without_prompt(tmp_path: Path, mailbox: str) -> None:
    provider = _FakeMailboxService()
    tool = next(
        tool
        for tool in _mailbox_tools(tmp_path, provider)
        if tool.name == READ_EMAIL_TOOL_NAME
    )

    with patch.object(email_messages, "interact_with_user") as prompt:
        result = tool(
            ReadEmail(mailbox=mailbox, source_message_ref="source"),
            messages=[],
        )

    prompt.assert_not_called()
    assert provider.read_mailboxes == [EmailMailbox(mailbox)]
    assert "BEGIN EMAIL BODY" in result.value
    assert "UNTRUSTED" not in result.value


def test_download_email_attachment_uses_guarded_context_base(
    tmp_path: Path,
) -> None:
    provider = _FakeMailboxService()
    tools = _mailbox_tools(tmp_path, provider)
    entry_index = next(
        index
        for index, tool in enumerate(tools)
        if tool.name == DOWNLOAD_EMAIL_ATTACHMENT_TOOL_NAME
    )
    destination = tmp_path / "downloads" / "notes.txt"
    destination.parent.mkdir()

    _, guarded, result = _run_chain(
        tools[entry_index : entry_index + 3],
        DownloadEmailAttachment(
            attachment_ref="opaque-attachment",
            destination_path="downloads/notes.txt",
        ),
    )

    assert guarded is not None and guarded.status is GuardStatus.ALLOWED
    assert result is not None and "Downloaded" in result.value
    assert destination.read_bytes() == b"notes"
    assert provider.download_requests == ["opaque-attachment"]


def test_create_reply_draft_uses_source_reference_and_guarded_attachments(
    tmp_path: Path,
) -> None:
    attachment = tmp_path / "notes.txt"
    attachment.write_text("notes", encoding="utf-8")
    provider = _FakeMailboxService()
    tools = _mailbox_tools(tmp_path, provider)

    _, guarded, result = _run_chain(
        tools[-3:],
        CreateReplyDraft(
            source_message_ref="proton-imap-message:v1:test",
            body_text="Thanks.",
            client_request_id="reply-1",
            attachment_paths=["notes.txt"],
        ),
    )

    assert guarded is not None
    assert result is not None
    assert provider.reply_requests == [
        EmailReplyDraftRequest(
            source_message_ref="proton-imap-message:v1:test",
            body_text="Thanks.",
            from_address=None,
            client_request_id="reply-1",
            attachments=(
                EmailDraftAttachment(
                    path=str(attachment.resolve()), filename="notes.txt"
                ),
            ),
        )
    ]
    assert "provider-reply-1" in result.value
    assert "Re: Project update" in result.value
    assert "Cc: copy@example.com" in result.value
    assert "Quoted original: included" in result.value
    assert "not been sent" in result.value


def test_create_reply_draft_can_opt_out_of_quoted_original(tmp_path: Path) -> None:
    provider = _FakeMailboxService()
    tools = _mailbox_tools(tmp_path, provider)

    _, _, result = _run_chain(
        tools[-3:],
        CreateReplyDraft(
            source_message_ref="proton-imap-message:v1:test",
            body_text="Clean reply.",
            include_quoted_original=False,
        ),
    )

    assert result is not None
    assert provider.reply_requests[0].include_quoted_original is False
    assert "Quoted original: not included" in result.value


def test_create_reply_draft_schema_does_not_accept_derived_headers() -> None:
    with pytest.raises(ValidationError):
        CreateReplyDraft.model_validate(
            {
                "source_message_ref": "source",
                "body_text": "Reply",
                "to": ["attacker@example.com"],
                "subject": "Injected",
                "in_reply_to": "<fake@example.com>",
            }
        )


def test_email_skill_requires_fresh_search_after_rejected_source() -> None:
    assert "stable sender address" in EMAIL_INSTRUCTIONS
    assert "invalidate that" in EMAIL_INSTRUCTIONS
    assert "fresh" in EMAIL_INSTRUCTIONS
    assert "read-only search" in EMAIL_INSTRUCTIONS
    assert "rejected" in EMAIL_INSTRUCTIONS
    assert "reference" in EMAIL_INSTRUCTIONS
    assert "as a way to inspect" in EMAIL_INSTRUCTIONS


class _ProbeService(_FakeMailboxService):
    def __init__(self, result=None, error=None):
        super().__init__()
        self.probe_calls = 0
        self.probe_result = {} if result is None else result
        self.probe_error = error

    def probe(self):
        self.probe_calls += 1
        if self.probe_error is not None:
            raise self.probe_error
        return self.probe_result


def test_email_dependencies_are_inspected_without_mailbox_work_then_monitored(tmp_path):
    service = _ProbeService()
    tools = _tools(tmp_path, service)
    reports = [tool.external_dependencies() for tool in tools]
    assert sum(bool(report) for report in reports) == 5
    assert all(report == () or report == (service,) for report in reports)
    for tool in tools:
        for resource in tool.copy().external_dependencies():
            assert resource is service
    agent = Agent(
        name="email_worker", mode=AgentMode.DETERMINISTIC, agent_endpoint=None,
        default_tools=(stop,), tools=tools,
    )
    assert agent.external_dependencies() == (service,)
    assert agent.external_dependencies()[0] is service
    monitor = DependencyHealthMonitor(agent.external_dependencies())
    assert service.probe_calls == 0
    assert monitor.records()[0].status is DependencyStatus.PENDING

    asyncio.run(monitor.run_once())

    assert service.probe_calls == 1
    assert service.kind is ExternalDependencyKind.NETWORK_SERVICE
    assert monitor.records()[0].status is DependencyStatus.AVAILABLE
    assert service.draft_requests == service.search_requests == service.reply_requests == []
    assert service.read_requests == service.download_requests == []


@pytest.mark.parametrize("result", [False, [], "available"])
def test_email_check_rejects_invalid_probe_results(result):
    service = _ProbeService(result=result)
    with pytest.raises(TypeError, match="probe.*dictionary"):
        service.check()
    assert service.probe_calls == 1


def test_email_check_propagates_probe_failure():
    error = ConnectionError("service unavailable")
    service = _ProbeService(error=error)
    with pytest.raises(ConnectionError) as raised:
        service.check()
    assert raised.value is error
    assert service.probe_calls == 1


def test_email_service_requires_identity_metadata_and_mailbox_operations():
    class Incomplete(EmailService):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()


@pytest.mark.parametrize("service", [object(), ExecutableDependency("python")])
def test_email_builder_requires_the_complete_service_contract(tmp_path, service):
    with pytest.raises(TypeError, match="EmailService"):
        get_work_with_email(service=service, base=tmp_path, default_verdict=ActionVerdict.deny)
