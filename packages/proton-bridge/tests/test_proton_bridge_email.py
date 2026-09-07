from __future__ import annotations

import imaplib
import importlib
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

import pytest

from roboshed.identifiers import (
    CREATE_EMAIL_DRAFT_TOOL_NAME,
    CREATE_REPLY_DRAFT_TOOL_NAME,
    DOWNLOAD_EMAIL_ATTACHMENT_TOOL_NAME,
    READ_EMAIL_TOOL_NAME,
    SEARCH_EMAIL_TOOL_NAME,
)
from roboshed.models import ActionVerdict
from roboshed.tools.email import (
    EmailDraftAttachment,
    EmailDraftRequest,
    EmailInlineImage,
    EmailMailbox,
    EmailProviderError,
    EmailReplyDraftRequest,
    EmailSearchRequest,
    EmailSignature,
)
from roboshed.tools.email.factory import get_work_with_email
from roboz_proton_bridge import (
    ProtonBridgeEmailService,
    ProtonBridgeSettings,
    ProtonBridgeTlsMode,
)
from roboz_proton_bridge.message_body import extract_message_body
from roboz_proton_bridge.mime import (
    build_draft_message,
    build_reply_draft_message,
)
from roboz_proton_bridge.models import ReplySourceHeaders

search_module = importlib.import_module("roboz_proton_bridge.search")


class _FakeImap:
    def __init__(self) -> None:
        self.logged_out = False
        self.append_calls: list[tuple[str, str, object, bytes]] = []
        self.existing_uids: bytes = b""
        self.search_uids: bytes = b""
        self.search_calls: list[tuple[object, ...]] = []
        self.fetch_calls: list[tuple[object, ...]] = []
        self.selected: list[tuple[str, bool]] = []
        self.uid_validity = 77
        self.fetched_headers: dict[bytes, bytes] = {}
        self.fetched_messages: dict[bytes, bytes] = {}
        self.internal_dates: dict[bytes, bytes | None] = {}
        self.seen_uids: set[bytes] = set()
        self.store_calls: list[tuple[object, ...]] = []

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        assert (user, password) == ("bridge-user", "bridge-password")
        return "OK", [b"logged in"]

    def list(
        self, directory: str = '""', pattern: str = "*"
    ) -> tuple[str, list[bytes | None]]:
        del directory, pattern
        return "OK", [
            b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
            b'(\\HasNoChildren \\Sent) "/" "Sent"',
        ]

    def append(
        self, mailbox: str, flags: str, date_time: object, message: bytes
    ) -> tuple[str, list[bytes]]:
        self.append_calls.append((mailbox, flags, date_time, message))
        return "OK", [b"[APPENDUID 9 42]"]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected.append((mailbox, readonly))
        return "OK", [b"1"]

    def uid(self, command: str, *args: object) -> tuple[str, list[bytes]]:
        if command == "SEARCH":
            if args[:3] == (None, "HEADER", "X-Peffa-Request-Id"):
                return "OK", [self.existing_uids]
            self.search_calls.append(args)
            return "OK", [self.search_uids]
        if command == "FETCH":
            self.fetch_calls.append(args)
            uid = args[0]
            assert isinstance(uid, bytes)
            query = str(args[1])
            if query in {"(UID INTERNALDATE)", "(UID INTERNALDATE FLAGS)"}:
                response: list[bytes] = []
                for index, candidate in enumerate(uid.split(b","), start=1):
                    received_at = self.internal_dates.get(
                        candidate, b"24-Jul-2026 11:20:30 +0300"
                    )
                    if received_at is not None:
                        response.append(
                            str(index).encode("ascii")
                            + b" (UID "
                            + candidate
                            + b' INTERNALDATE "'
                            + received_at
                            + b'")'
                            + (
                                b" FLAGS (\\Seen)"
                                if candidate in self.seen_uids
                                else b" FLAGS ()"
                            )
                        )
                return "OK", response
            payload = (
                self.fetched_messages.get(uid)
                if "BODY.PEEK[]" in query
                else self.fetched_headers.get(uid)
            )
            if payload is None:
                return "OK", [b")"]
            received_at = self.internal_dates.get(uid, b"24-Jul-2026 11:20:30 +0300")
            assert received_at is not None
            metadata = (
                b"1 (UID "
                + uid
                + b' INTERNALDATE "'
                + received_at
                + b'" '
                + b"BODY[] {"
                + str(len(payload)).encode("ascii")
                + b"}"
            )
            return "OK", [(metadata, payload), b")"]  # type: ignore[list-item]
        if command == "STORE":
            self.store_calls.append(args)
            uid = args[0]
            assert isinstance(uid, bytes)
            self.seen_uids.add(uid)
            return "OK", [b"stored"]
        raise AssertionError(f"unexpected UID command: {command}")

    def response(self, code: str) -> tuple[str, list[bytes]]:
        assert code == "UIDVALIDITY"
        return code, [str(self.uid_validity).encode("ascii")]

    def logout(self) -> tuple[str, list[bytes]]:
        self.logged_out = True
        return "BYE", [b"logout"]

    def starttls(self, ssl_context: object) -> tuple[str, list[bytes]]:
        del ssl_context
        return "OK", [b"TLS"]


def _service(
    connection: _FakeImap,
    *,
    signature: EmailSignature | None = None,
) -> ProtonBridgeEmailService:
    settings = ProtonBridgeSettings(
        imap_host="127.0.0.1",
        imap_port=1143,
        tls_mode=ProtonBridgeTlsMode.STARTTLS,
        account_address="me@example.com",
        username="bridge-user",
        password="bridge-password",
    )
    return ProtonBridgeEmailService(
        settings,
        signature=signature or _signature(),
        imap_factory=lambda settings, context: connection,
    )


def _signature(*, warnings: tuple[str, ...] = ()) -> EmailSignature:
    return EmailSignature(
        plain_text="Tommi Markkanen\nCTO\nTachion",
        html=(
            "<strong>Tommi Markkanen</strong><br>CTO<br>"
            '<img src="cid:peffahub-signature-image" alt="Tachion">'
        ),
        inline_image=EmailInlineImage(
            data=b"signature image",
            filename="tachion-wordmark-email-2x.png",
            content_type="image/png",
            content_id="peffahub-signature-image",
        ),
        warnings=warnings,
    )


def _request(
    *, attachments: tuple[EmailDraftAttachment, ...] = ()
) -> EmailDraftRequest:
    return EmailDraftRequest(
        to=("person@example.com",),
        cc=("copy@example.com",),
        bcc=("hidden@example.com",),
        subject="Draft subject",
        body_text="Draft body",
        from_address=None,
        reply_to=None,
        client_request_id="request-1",
        attachments=attachments,
    )


def test_proton_bridge_reads_connection_and_credentials_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprint = "a2" * 32
    monkeypatch.setenv("ROBOZ_PROTON_BRIDGE_IMAP_HOST", "127.0.0.1")
    monkeypatch.setenv("ROBOZ_PROTON_BRIDGE_IMAP_PORT", "1143")
    monkeypatch.setenv("ROBOZ_PROTON_BRIDGE_TLS_MODE", "starttls")
    monkeypatch.setenv("ROBOZ_PROTON_BRIDGE_ACCOUNT_ADDRESS", "me@example.com")
    monkeypatch.setenv("ROBOZ_PROTON_BRIDGE_CERTIFICATE_SHA256", fingerprint)
    monkeypatch.setenv("ROBOZ_PROTON_BRIDGE_USERNAME", "bridge-user")
    monkeypatch.setenv("ROBOZ_PROTON_BRIDGE_PASSWORD", "bridge-password")

    settings = ProtonBridgeSettings.from_environment()

    assert settings.imap_host == "127.0.0.1"
    assert settings.imap_port == 1143
    assert settings.tls_mode is ProtonBridgeTlsMode.STARTTLS
    assert settings.account_address == "me@example.com"
    assert settings.certificate_sha256 == fingerprint
    assert settings.username == "bridge-user"
    assert settings.password.get_secret_value() == "bridge-password"


def test_proton_bridge_provider_creates_a_complete_sender_side_draft() -> None:
    connection = _FakeImap()

    result = _service(connection).create_draft(_request(), is_cancelled=lambda: False)

    assert result.draft_id == "imap-uid:42"
    assert result.account_address == "me@example.com"
    assert connection.logged_out
    mailbox, flags, _, raw_message = connection.append_calls[0]
    assert mailbox == "Drafts"
    assert flags == r"(\Draft)"
    message = raw_message.decode("utf-8")
    assert "To: person@example.com" in message
    assert "Cc: copy@example.com" in message
    assert "Bcc: hidden@example.com" in message
    assert "X-Peffa-Request-Id: request-1" in message


def test_proton_bridge_embeds_signature_in_plain_and_html_draft() -> None:
    connection = _FakeImap()

    _service(connection).create_draft(_request(), is_cancelled=lambda: False)

    message = BytesParser(policy=policy.default).parsebytes(
        connection.append_calls[0][3]
    )
    plain_body = message.get_body(preferencelist=("plain",))
    html_body = message.get_body(preferencelist=("html",))
    assert plain_body is not None
    assert plain_body.get_content() == ("Draft body\n\nTommi Markkanen\nCTO\nTachion\n")
    assert html_body is not None
    html = html_body.get_content()
    assert "Draft body" in html
    assert "Tommi Markkanen" in html
    assert 'src="cid:peffahub-signature-image"' in html
    (image,) = [part for part in message.walk() if part["Content-ID"] is not None]
    assert image.get_content_type() == "image/png"
    assert image["Content-ID"] == "<peffahub-signature-image>"
    assert image.get_content_disposition() == "inline"
    assert image.get_filename() == "tachion-wordmark-email-2x.png"
    assert image.get_payload(decode=True) == b"signature image"


def test_proton_bridge_refuses_to_create_an_unsigned_draft() -> None:
    connection = _FakeImap()
    settings = ProtonBridgeSettings(
        imap_host="127.0.0.1",
        imap_port=1143,
        tls_mode=ProtonBridgeTlsMode.STARTTLS,
        account_address="me@example.com",
        username="bridge-user",
        password="bridge-password",
    )
    service = ProtonBridgeEmailService(
        settings,
        imap_factory=lambda settings, context: connection,
    )

    with pytest.raises(EmailProviderError, match="signature is not configured"):
        service.create_draft(_request(), is_cancelled=lambda: False)

    assert connection.append_calls == []


def test_proton_bridge_reports_signature_fallback_warning() -> None:
    connection = _FakeImap()
    warning = "Rich signature unavailable; used text fallback."

    result = _service(
        connection,
        signature=_signature(warnings=(warning,)),
    ).create_draft(_request(), is_cancelled=lambda: False)

    assert result.warnings == (warning,)


def test_proton_bridge_probe_is_read_only() -> None:
    connection = _FakeImap()

    result = _service(connection).probe()

    assert result["drafts_mailbox"] == "Drafts"
    assert connection.append_calls == []
    assert connection.logged_out


def test_proton_bridge_bundle_owns_the_final_tool_dependency(tmp_path: Path) -> None:
    connection = _FakeImap()
    settings = ProtonBridgeSettings(
        imap_host="127.0.0.1",
        imap_port=1143,
        tls_mode=ProtonBridgeTlsMode.STARTTLS,
        account_address="me@example.com",
        username="bridge-user",
        password="bridge-password",
    )
    service = ProtonBridgeEmailService(
        settings,
        imap_factory=lambda settings, context: connection,
    )
    tools = get_work_with_email(
        service=service,
        base=tmp_path,
        default_verdict=ActionVerdict.allow,
        email_skill_name="email_tools",
    )

    dependencies = {
        dependency.dependency_id
        for tool in tools
        for dependency in tool.external_dependencies
    }
    assert dependencies == {"network:proton_bridge"}
    assert {tool.name for tool in tools if tool.chained_to is None} == {
        CREATE_EMAIL_DRAFT_TOOL_NAME,
        SEARCH_EMAIL_TOOL_NAME,
        READ_EMAIL_TOOL_NAME,
        DOWNLOAD_EMAIL_ATTACHMENT_TOOL_NAME,
        CREATE_REPLY_DRAFT_TOOL_NAME,
    }
    assert connection.append_calls == []


def test_proton_bridge_reuses_draft_with_same_request_id() -> None:
    connection = _FakeImap()
    connection.existing_uids = b"12 27"

    result = _service(connection).create_draft(_request(), is_cancelled=lambda: False)

    assert result.draft_id == "imap-uid:27"
    assert connection.append_calls == []
    assert "existing draft" in result.warnings[0]


def _source_headers(*, message_id: str = "<parent-42@example.com>") -> bytes:
    return (
        b'From: "Alice Example" <alice@example.com>\r\n'
        b'Reply-To: "Alice Replies" <reply@example.com>\r\n'
        b"To: me@example.com\r\n"
        b"Date: Fri, 24 Jul 2026 11:20:30 +0300\r\n"
        b"Subject: Project update\r\n"
        + f"Message-ID: {message_id}\r\n".encode("ascii")
        + b"References: <root@example.com>\r\n"
        + b"\r\n"
    )


def _source_message(*, message_id: str = "<parent-42@example.com>") -> bytes:
    return (
        _source_headers(message_id=message_id)
        + b"Original message first line.\r\n"
        + b"Original message second line.\r\n"
    )


def test_proton_bridge_searches_inbox_without_marking_messages_read() -> None:
    connection = _FakeImap()
    connection.search_uids = b"41 42"
    connection.fetched_headers = {
        b"41": _source_headers(message_id="<parent-41@example.com>"),
        b"42": _source_headers(),
    }

    results = _service(connection).search_messages(
        EmailSearchRequest(
            from_address="alice@example.com",
            subject_contains="Project",
            limit=1,
        ),
        is_cancelled=lambda: False,
    )

    assert len(results) == 1
    assert results[0].sender == "Alice Example <alice@example.com>"
    assert results[0].subject == "Project update"
    assert results[0].replyable
    assert results[0].timestamp is not None
    assert connection.selected[0] == ("INBOX", True)
    assert connection.search_calls == [
        (
            "CHARSET",
            "UTF-8",
            "FROM",
            b'"alice@example.com"',
            "SUBJECT",
            b'"Project"',
        )
    ]
    assert connection.fetch_calls[0] == (b"41,42", "(UID INTERNALDATE FLAGS)")
    assert connection.fetch_calls[1][0] == b"42"
    assert "BODY.PEEK[HEADER.FIELDS" in str(connection.fetch_calls[1][1])
    assert connection.append_calls == []


@pytest.mark.parametrize(
    ("mailbox", "physical_mailbox"),
    [
        (EmailMailbox.DRAFTS, "Drafts"),
        (EmailMailbox.SENT, "Sent"),
    ],
)
def test_proton_bridge_searches_and_reads_user_authored_mailboxes_read_only(
    mailbox: EmailMailbox,
    physical_mailbox: str,
) -> None:
    connection = _FakeImap()
    connection.search_uids = b"42"
    connection.fetched_headers[b"42"] = (
        b"From: me@example.com\r\n"
        b"To: alice@example.com\r\n"
        b"Bcc: archive@example.com\r\n"
        b"Subject: Authored message\r\n\r\n"
    )
    connection.fetched_messages[b"42"] = (
        connection.fetched_headers[b"42"] + b"Safe authored body.\r\n"
    )
    service = _service(connection)

    (summary,) = service.search_messages(
        EmailSearchRequest(
            mailbox=mailbox,
            to_address="alice@example.com",
            limit=1,
        ),
        is_cancelled=lambda: False,
    )
    message = service.read_message(
        mailbox,
        summary.source_message_ref,
        is_cancelled=lambda: False,
    )

    assert summary.mailbox is mailbox
    assert not summary.replyable
    assert message.mailbox is mailbox
    assert message.bcc == ("archive@example.com",)
    assert message.body_text == "Safe authored body."
    assert connection.search_calls == [
        ("CHARSET", "UTF-8", "TO", b'"alice@example.com"')
    ]
    assert connection.selected == [
        (physical_mailbox, True),
        (physical_mailbox, True),
    ]
    assert connection.store_calls == []


def test_proton_bridge_rejects_mailbox_reference_mismatch() -> None:
    connection = _FakeImap()
    connection.search_uids = b"42"
    connection.fetched_headers[b"42"] = _source_headers()
    service = _service(connection)
    (summary,) = service.search_messages(
        EmailSearchRequest(mailbox=EmailMailbox.INBOX, limit=1),
        is_cancelled=lambda: False,
    )

    with pytest.raises(EmailProviderError, match="requested mailbox"):
        service.read_message(
            EmailMailbox.DRAFTS,
            summary.source_message_ref,
            is_cancelled=lambda: False,
        )

    assert connection.store_calls == []


def test_proton_bridge_search_returns_seen_status_and_bounded_preview() -> None:
    connection = _FakeImap()
    connection.search_uids = b"42"
    connection.seen_uids.add(b"42")
    connection.fetched_headers[b"42"] = _source_headers()
    connection.fetched_messages[b"42"] = _source_headers() + (b"Preview content " * 30)

    (result,) = _service(connection).search_messages(
        EmailSearchRequest(limit=1),
        is_cancelled=lambda: False,
    )

    assert result.is_read
    assert result.preview_text is not None
    assert result.preview_text.startswith("Preview content")
    assert 150 <= len(result.preview_text) <= 160
    assert connection.store_calls == []
    assert connection.selected == [("INBOX", True)]


def test_proton_bridge_full_read_marks_seen_and_downloads_selected_attachment() -> None:
    connection = _FakeImap()
    connection.search_uids = b"42"
    connection.fetched_headers[b"42"] = _source_headers()
    message = EmailMessage()
    message["From"] = "Alice <alice@example.com>"
    message["To"] = "me@example.com"
    message["Subject"] = "Project update"
    message["Message-ID"] = "<parent-42@example.com>"
    message.set_content("The complete message body.")
    message.add_attachment(
        b"attachment bytes",
        maintype="application",
        subtype="octet-stream",
        filename="../unsafe-name.bin",
    )
    connection.fetched_messages[b"42"] = message.as_bytes(policy=policy.default)
    service = _service(connection)
    (summary,) = service.search_messages(
        EmailSearchRequest(limit=1), is_cancelled=lambda: False
    )

    result = service.read_message(
        EmailMailbox.INBOX,
        summary.source_message_ref,
        is_cancelled=lambda: False,
    )

    assert result.body_text == "The complete message body."
    assert result.attachments[0].filename == "../unsafe-name.bin"
    assert result.attachments[0].size == len(b"attachment bytes")
    assert connection.store_calls == [(b"42", "+FLAGS.SILENT", r"(\Seen)")]
    assert connection.selected[-1] == ("INBOX", False)

    downloaded = service.download_attachment(
        result.attachments[0].attachment_ref,
        is_cancelled=lambda: False,
    )

    assert downloaded.data == b"attachment bytes"
    assert downloaded.filename == "../unsafe-name.bin"
    assert connection.selected[-1] == ("INBOX", True)
    assert len(connection.store_calls) == 1


def test_proton_bridge_orders_search_by_internaldate_not_uid() -> None:
    connection = _FakeImap()
    connection.search_uids = b"13 14 31 71"
    connection.internal_dates = {
        b"13": b"10-Jul-2026 09:34:42 +0000",
        b"14": b"10-Jul-2026 08:48:04 +0000",
        b"31": b"02-Jul-2026 09:50:17 +0000",
        b"71": b"12-Jun-2026 12:49:22 +0000",
    }
    connection.fetched_headers = {
        uid: _source_headers(message_id=f"<parent-{uid.decode()}@example.com>")
        for uid in (b"13", b"14", b"31", b"71")
    }

    results = _service(connection).search_messages(
        EmailSearchRequest(limit=3),
        is_cancelled=lambda: False,
    )

    assert [item.timestamp.isoformat() for item in results if item.timestamp] == [
        "2026-07-10T09:34:42+00:00",
        "2026-07-10T08:48:04+00:00",
        "2026-07-02T09:50:17+00:00",
    ]
    assert [
        call[0]
        for call in connection.fetch_calls[1:]
        if "HEADER.FIELDS" in str(call[1])
    ] == [
        b"13",
        b"14",
        b"31",
    ]


def test_proton_bridge_search_uses_uid_tie_break_and_skips_bad_headers() -> None:
    connection = _FakeImap()
    connection.search_uids = b"40 41 42"
    connection.internal_dates = {
        uid: b"24-Jul-2026 11:20:30 +0300" for uid in (b"40", b"41", b"42")
    }
    connection.fetched_headers = {
        b"40": _source_headers(message_id="<parent-40@example.com>"),
        b"41": _source_headers(message_id="<parent-41@example.com>"),
    }

    results = _service(connection).search_messages(
        EmailSearchRequest(limit=2),
        is_cancelled=lambda: False,
    )

    assert [item.source_message_ref.rsplit(":", 1)[-1] for item in results]
    assert [
        call[0]
        for call in connection.fetch_calls[1:]
        if "HEADER.FIELDS" in str(call[1])
    ] == [
        b"42",
        b"41",
        b"40",
    ]
    assert len(results) == 2


def test_proton_bridge_search_rejects_excessive_metadata_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeImap()
    connection.search_uids = b"1 2 3"
    monkeypatch.setattr(
        search_module,
        "MAX_SEARCH_METADATA_CANDIDATES",
        2,
    )

    with pytest.raises(EmailProviderError, match="Too many emails"):
        _service(connection).search_messages(
            EmailSearchRequest(limit=1),
            is_cancelled=lambda: False,
        )

    assert connection.fetch_calls == []


def test_proton_bridge_search_batches_metadata_and_skips_missing_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeImap()
    connection.search_uids = b"1 2 3"
    connection.internal_dates = {
        b"1": b"01-Jul-2026 10:00:00 +0000",
        b"2": None,
        b"3": b"03-Jul-2026 10:00:00 +0000",
    }
    connection.fetched_headers = {
        b"1": _source_headers(message_id="<parent-1@example.com>"),
        b"3": _source_headers(message_id="<parent-3@example.com>"),
    }
    monkeypatch.setattr(search_module, "SEARCH_METADATA_BATCH_SIZE", 2)

    results = _service(connection).search_messages(
        EmailSearchRequest(limit=2),
        is_cancelled=lambda: False,
    )

    assert connection.fetch_calls[:2] == [
        (b"1,2", "(UID INTERNALDATE FLAGS)"),
        (b"3", "(UID INTERNALDATE FLAGS)"),
    ]
    assert [
        call[0]
        for call in connection.fetch_calls[2:]
        if "HEADER.FIELDS" in str(call[1])
    ] == [b"3", b"1"]
    assert len(results) == 2


def test_proton_bridge_search_rejects_matches_without_received_dates() -> None:
    connection = _FakeImap()
    connection.search_uids = b"1 2"
    connection.internal_dates = {b"1": None, b"2": None}

    with pytest.raises(EmailProviderError, match="did not return dates"):
        _service(connection).search_messages(
            EmailSearchRequest(limit=1),
            is_cancelled=lambda: False,
        )

    assert connection.fetch_calls == [(b"1,2", "(UID INTERNALDATE FLAGS)")]


def test_proton_bridge_search_checks_cancellation_between_metadata_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeImap()
    connection.search_uids = b"1 2 3"
    monkeypatch.setattr(search_module, "SEARCH_METADATA_BATCH_SIZE", 2)
    checks = 0

    def is_cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(EmailProviderError, match="email operation was cancelled"):
        _service(connection).search_messages(
            EmailSearchRequest(limit=1),
            is_cancelled=is_cancelled,
        )

    assert connection.fetch_calls == [(b"1,2", "(UID INTERNALDATE FLAGS)")]


def test_proton_bridge_reports_protocol_rejection() -> None:
    class _RejectingImap(_FakeImap):
        def select(
            self, mailbox: str, readonly: bool = False
        ) -> tuple[str, list[bytes]]:
            del mailbox, readonly
            raise imaplib.IMAP4.error("UID command error: BAD")

    with pytest.raises(EmailProviderError, match="rejected the IMAP operation"):
        _service(_RejectingImap()).search_messages(
            EmailSearchRequest(limit=1),
            is_cancelled=lambda: False,
        )


def test_proton_bridge_creates_threaded_reply_draft_from_search_reference() -> None:
    connection = _FakeImap()
    connection.search_uids = b"42"
    connection.fetched_headers[b"42"] = _source_headers()
    connection.fetched_messages[b"42"] = _source_message()
    service = _service(connection)
    (summary,) = service.search_messages(
        EmailSearchRequest(limit=1), is_cancelled=lambda: False
    )

    result = service.create_reply_draft(
        EmailReplyDraftRequest(
            source_message_ref=summary.source_message_ref,
            body_text="Thanks, this works for me.",
            from_address=None,
            client_request_id="reply-request-1",
        ),
        is_cancelled=lambda: False,
    )

    assert result.to == ("reply@example.com",)
    assert result.cc == ()
    assert result.subject == "Re: Project update"
    raw_message = connection.append_calls[0][3]
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    assert str(message["To"]) == "reply@example.com"
    assert str(message["Subject"]) == "Re: Project update"
    assert str(message["In-Reply-To"]) == "<parent-42@example.com>"
    assert str(message["References"]) == ("<root@example.com> <parent-42@example.com>")
    body = message.get_body(preferencelist=("plain",))
    assert body is not None
    assert body.get_content() == (
        "Thanks, this works for me.\n\n"
        "Tommi Markkanen\nCTO\nTachion\n\n"
        "On Fri, 24 Jul 2026 11:20:30 +0300, "
        "Alice Example <alice@example.com> wrote:\n"
        "> Original message first line.\n"
        "> Original message second line.\n"
    )
    html_body = message.get_body(preferencelist=("html",))
    assert html_body is not None
    html = html_body.get_content()
    assert '<div class="protonmail_quote">' in html
    assert '<blockquote class="protonmail_quote" type="cite">' in html
    assert "Original message first line.<br>" in html
    assert result.quoted_original_included
    assert any(
        "BODY.PEEK[]<0.1000001>" in str(call[1]) for call in connection.fetch_calls
    )


def test_proton_bridge_reply_all_preserves_to_and_cc_without_self_or_bcc() -> None:
    connection = _FakeImap()
    connection.search_uids = b"42"
    connection.fetched_headers[b"42"] = (
        b'From: "Alice Example" <alice@example.com>\r\n'
        b'Reply-To: "Alice Replies" <reply@example.com>\r\n'
        b"To: me@example.com, Bob <bob@example.com>, REPLY@example.com\r\n"
        b"Cc: Carol <carol@example.com>, BOB@example.com, ME@example.com\r\n"
        b"Bcc: hidden@example.com\r\n"
        b"Subject: Project update\r\n"
        b"Message-ID: <parent-42@example.com>\r\n"
        b"\r\n"
    )
    service = _service(connection)
    (summary,) = service.search_messages(
        EmailSearchRequest(limit=1), is_cancelled=lambda: False
    )

    result = service.create_reply_draft(
        EmailReplyDraftRequest(
            source_message_ref=summary.source_message_ref,
            body_text="Reply all.",
            from_address=None,
            client_request_id=None,
        ),
        is_cancelled=lambda: False,
    )

    assert result.to == ("reply@example.com", "bob@example.com")
    assert result.cc == ("carol@example.com",)
    message = BytesParser(policy=policy.default).parsebytes(
        connection.append_calls[0][3]
    )
    assert tuple(address.addr_spec for address in message["To"].addresses) == (
        "reply@example.com",
        "bob@example.com",
    )
    assert tuple(address.addr_spec for address in message["Cc"].addresses) == (
        "carol@example.com",
    )
    assert message["Bcc"] is None
    header_fetch = str(connection.fetch_calls[1][1])
    assert "To" in header_fetch
    assert "Cc" in header_fetch
    assert "Bcc" in header_fetch


def test_proton_bridge_can_create_sender_only_reply() -> None:
    connection = _FakeImap()
    connection.search_uids = b"42"
    connection.fetched_headers[b"42"] = (
        b"From: alice@example.com\r\n"
        b"To: me@example.com, bob@example.com\r\n"
        b"Cc: carol@example.com\r\n"
        b"Subject: Project update\r\n"
        b"Message-ID: <parent-42@example.com>\r\n"
        b"\r\n"
    )
    service = _service(connection)
    (summary,) = service.search_messages(
        EmailSearchRequest(limit=1), is_cancelled=lambda: False
    )

    result = service.create_reply_draft(
        EmailReplyDraftRequest(
            source_message_ref=summary.source_message_ref,
            body_text="Private reply.",
            from_address=None,
            client_request_id=None,
            reply_all=False,
        ),
        is_cancelled=lambda: False,
    )

    assert result.to == ("alice@example.com",)
    assert result.cc == ()
    message = BytesParser(policy=policy.default).parsebytes(
        connection.append_calls[0][3]
    )
    assert str(message["To"]) == "alice@example.com"
    assert message["Cc"] is None


def test_proton_bridge_reply_can_explicitly_omit_quoted_original() -> None:
    connection = _FakeImap()
    connection.search_uids = b"42"
    connection.fetched_headers[b"42"] = _source_headers()
    connection.fetched_messages[b"42"] = _source_message()
    service = _service(connection)
    (summary,) = service.search_messages(
        EmailSearchRequest(limit=1), is_cancelled=lambda: False
    )
    fetch_count_after_search = len(connection.fetch_calls)

    result = service.create_reply_draft(
        EmailReplyDraftRequest(
            source_message_ref=summary.source_message_ref,
            body_text="A clean reply.",
            from_address=None,
            client_request_id=None,
            include_quoted_original=False,
        ),
        is_cancelled=lambda: False,
    )

    message = BytesParser(policy=policy.default).parsebytes(
        connection.append_calls[0][3]
    )
    body = message.get_body(preferencelist=("plain",))
    assert body is not None
    assert body.get_content() == ("A clean reply.\n\nTommi Markkanen\nCTO\nTachion\n")
    assert message.get_body(preferencelist=("html",)) is not None
    assert not result.quoted_original_included
    assert all(
        "BODY.PEEK[]" not in str(call[1])
        for call in connection.fetch_calls[fetch_count_after_search:]
    )


def test_message_body_extraction_falls_back_to_html_without_active_content() -> None:
    extracted = extract_message_body(
        (
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"\r\n"
            b"<html><head><style>.hidden { display: none; }</style></head>"
            b"<body><p>Hello <strong>world</strong>.</p>"
            b"<script>doSomethingDangerous()</script><div>Second line</div></body></html>"
        ),
        source_truncated=True,
    )

    assert extracted.text == "Hello world.\nSecond line"
    assert extracted.truncated


def test_reply_html_escapes_new_and_quoted_message_content() -> None:
    raw = build_reply_draft_message(
        EmailReplyDraftRequest(
            source_message_ref="source",
            body_text="<b>Reply & safe</b>",
            from_address=None,
            client_request_id=None,
        ),
        ReplySourceHeaders(
            to=("alice@example.com",),
            cc=(),
            subject="Subject",
            message_id="<parent@example.com>",
            references=("<parent@example.com>",),
            sender="Alice <alice@example.com>",
            sent_at=None,
            quoted_body="<script>alert('no')</script> & quoted",
        ),
        default_from_address="me@example.com",
        signature=_signature(),
    )

    message = BytesParser(policy=policy.default).parsebytes(raw)
    html_body = message.get_body(preferencelist=("html",))
    assert html_body is not None
    html = html_body.get_content()
    assert "&lt;b&gt;Reply &amp; safe&lt;/b&gt;" in html
    assert "&lt;script&gt;alert(&#x27;no&#x27;)&lt;/script&gt; &amp; quoted" in html
    assert "<script>" not in html


def test_proton_bridge_rejects_stale_or_unthreadable_reply_sources() -> None:
    connection = _FakeImap()
    connection.search_uids = b"42"
    connection.fetched_headers[b"42"] = _source_headers()
    service = _service(connection)
    (summary,) = service.search_messages(
        EmailSearchRequest(limit=1), is_cancelled=lambda: False
    )

    connection.uid_validity = 78
    with pytest.raises(EmailProviderError, match="stale"):
        service.create_reply_draft(
            EmailReplyDraftRequest(
                source_message_ref=summary.source_message_ref,
                body_text="Reply",
                from_address=None,
                client_request_id=None,
            ),
            is_cancelled=lambda: False,
        )

    connection.uid_validity = 77
    connection.fetched_headers[b"42"] = _source_headers(message_id="invalid")
    with pytest.raises(EmailProviderError, match="no valid Message-ID"):
        service.create_reply_draft(
            EmailReplyDraftRequest(
                source_message_ref=summary.source_message_ref,
                body_text="Reply",
                from_address=None,
                client_request_id=None,
            ),
            is_cancelled=lambda: False,
        )


def test_proton_bridge_reply_falls_back_to_from_and_preserves_re_subject() -> None:
    connection = _FakeImap()
    connection.search_uids = b"42"
    connection.fetched_headers[b"42"] = (
        b'From: "Alice Example" <alice@example.com>\r\n'
        b"To: me@example.com\r\n"
        b"Subject: Re: Project update\r\n"
        b"Message-ID: <parent-42@example.com>\r\n"
        b"\r\n"
    )
    service = _service(connection)
    (summary,) = service.search_messages(
        EmailSearchRequest(limit=1), is_cancelled=lambda: False
    )

    result = service.create_reply_draft(
        EmailReplyDraftRequest(
            source_message_ref=summary.source_message_ref,
            body_text="Reply",
            from_address=None,
            client_request_id=None,
        ),
        is_cancelled=lambda: False,
    )

    assert result.to == ("alice@example.com",)
    assert result.subject == "Re: Project update"
    message = BytesParser(policy=policy.default).parsebytes(
        connection.append_calls[0][3]
    )
    assert str(message["References"]) == "<parent-42@example.com>"
    assert not result.quoted_original_included
    assert result.warnings == ("The original message body could not be quoted.",)


def test_proton_bridge_rejects_malformed_reply_source_reference() -> None:
    with pytest.raises(EmailProviderError, match="reference is invalid"):
        _service(_FakeImap()).create_reply_draft(
            EmailReplyDraftRequest(
                source_message_ref="not-a-source-reference",
                body_text="Reply",
                from_address=None,
                client_request_id=None,
            ),
            is_cancelled=lambda: False,
        )


def test_build_draft_message_includes_attachment_parts(tmp_path: Path) -> None:
    pdf = tmp_path / "CV.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    tex = tmp_path / "CV.tex"
    tex.write_text("\\documentclass{article}", encoding="utf-8")
    raw = build_draft_message(
        _request(
            attachments=(
                EmailDraftAttachment(path=str(pdf), filename="CV.pdf"),
                EmailDraftAttachment(path=str(tex), filename="CV.tex"),
            )
        ),
        default_from_address="me@example.com",
        signature=_signature(),
    )
    message = BytesParser(policy=policy.default).parsebytes(raw)

    assert message.get_content_maintype() == "multipart"
    attachments = list(message.iter_attachments())
    assert [part.get_filename() for part in attachments] == ["CV.pdf", "CV.tex"]
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_content() == b"%PDF-1.4 fake"
    assert attachments[1].get_payload(decode=True) == b"\\documentclass{article}"
    body = message.get_body(preferencelist=("plain",))
    assert body is not None
    assert "Draft body" in body.get_content()


def test_build_draft_message_rejects_unreadable_attachment(tmp_path: Path) -> None:
    missing = tmp_path / "gone.pdf"
    with pytest.raises(EmailProviderError, match="Unable to read attachment"):
        build_draft_message(
            _request(
                attachments=(
                    EmailDraftAttachment(path=str(missing), filename="gone.pdf"),
                )
            ),
            default_from_address="me@example.com",
            signature=_signature(),
        )


def test_proton_bridge_provider_appends_multipart_with_attachments(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("attached notes", encoding="utf-8")
    other = tmp_path / "extra.bin"
    other.write_bytes(b"\x00\x01")
    connection = _FakeImap()

    result = _service(connection).create_draft(
        _request(
            attachments=(
                EmailDraftAttachment(path=str(notes), filename="notes.txt"),
                EmailDraftAttachment(path=str(other), filename="extra.bin"),
            )
        ),
        is_cancelled=lambda: False,
    )

    assert result.draft_id == "imap-uid:42"
    raw_message = connection.append_calls[0][3]
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    attachments = list(message.iter_attachments())
    assert len(attachments) == 2
    assert attachments[0].get_filename() == "notes.txt"
    assert attachments[0].get_payload(decode=True) == b"attached notes"
    assert attachments[1].get_filename() == "extra.bin"
    assert attachments[1].get_payload(decode=True) == b"\x00\x01"
