from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from imaplib import IMAP4
from threading import Event

import pytest
from imapclient import IMAPClient
from pydantic import ValidationError

from roboz.exceptions import ExternalCallCancelledError, ExternalCallTimeoutError
from roboz.runtime.pipe import EventPipe
from roboz.shed.models import ActionVerdict
from roboz.shed.tools.contexts import EmailContext
from roboz.shed.tools.email import get_work_with_email
from roboz.shed.tools.email.contracts import (
    EmailDraftAttachment,
    EmailDraftRequest,
    EmailMailbox,
    EmailProviderError,
    EmailReplyDraftRequest,
    EmailSearchRequest,
    EmailSignature,
    EmailInlineImage,
)
from roboz.shed.tools.email.proton_bridge import (
    ProtonBridgeEmailService,
    ProtonBridgeSettings,
)
from roboz.shed.tools.email.proton_bridge.references import encode_source_reference
from roboz.shed.tools.email.runtime import run_email_call

NOW = datetime(2026, 7, 24, 8, 20, 30, tzinfo=timezone.utc)


def active():
    return False


class FakeClient(IMAPClient):
    def __init__(self):
        self.normalise_times = True
        self.folders = [
            ((b"\\Drafts",), b"/", "My Drafts"),
            ((b"\\Sent",), b"/", "Sent"),
        ]
        self.messages = {42: source_message()}
        self.dates = {42: NOW}
        self.seen = set()
        self.uids = [42]
        self.validity = 77
        self.selected = []
        self.appended = []
        self.fetches = []
        self.searches = []
        self.closed = False
        self.login_error = self.logout_error = self.fetch_error = self.append_error = (
            None
        )
        self.after_search = self.after_fetch = lambda: None
        self.append_response = b"[APPENDUID 77 99] done"

    def login(self, username, password):
        assert (username, password) == ("bridge-user", "bridge-password")
        if self.login_error:
            raise self.login_error

    def logout(self):
        if self.logout_error:
            raise self.logout_error
        self.closed = True

    def shutdown(self):
        self.closed = True

    def list_folders(self):
        return self.folders

    def select_folder(self, folder, readonly=False):
        self.selected.append((folder, readonly))
        return {b"UIDVALIDITY": self.validity}

    def search(self, criteria, charset):
        self.searches.append((criteria, charset))
        self.after_search()
        if criteria[0] == "HEADER":
            return [
                uid
                for uid, raw in self.messages.items()
                if criteria[-1].lower().encode() in raw.lower()
            ]
        return self.uids

    def fetch(self, messages, data):
        self.fetches.append((tuple(messages), tuple(data)))
        if self.fetch_error:
            raise self.fetch_error
        result = {}
        for uid in messages:
            if uid not in self.messages:
                continue
            values = {
                b"INTERNALDATE": self.dates.get(uid),
                b"FLAGS": (b"\\Seen",) if uid in self.seen else (),
            }
            for field in data:
                if field.startswith("BODY.PEEK["):
                    raw = self.messages[uid]
                    if "[HEADER]" in field:
                        raw = raw.split(b"\r\n\r\n")[0] + b"\r\n\r\n"
                    limit = int(field.rsplit(".", 1)[1][:-1])
                    key = field.replace("BODY.PEEK", "BODY").split("<")[0] + "<0>"
                    values[key.encode()] = raw[:limit]
            result[uid] = values
        self.after_fetch()
        return result

    def append(self, folder, msg, flags):
        self.appended.append((folder, msg, flags))
        if self.append_error:
            raise self.append_error
        return self.append_response

    def add_flags(self, messages, flags, silent):
        assert flags == [b"\\Seen"] and silent
        self.seen.update(messages)


def settings(**overrides):
    return ProtonBridgeSettings.model_validate(
        dict(
            imap_host="127.0.0.1",
            imap_port=1143,
            tls_mode="starttls",
            account_address="me@example.com",
            username="bridge-user",
            password="bridge-password",
            **overrides,
        )
    )


@pytest.fixture
def client():
    return FakeClient()


def service(client, signature=None):
    return ProtonBridgeEmailService(
        settings(), signature=signature, client_factory=lambda settings, context: client
    )


def draft(**overrides):
    return replace(
        EmailDraftRequest(
            to=("person@example.com",),
            cc=("copy@example.com",),
            bcc=("hidden@example.com",),
            subject="Draft subject",
            body_text="Draft body",
            from_address=None,
            reply_to="reply@example.com",
            client_request_id="request-1",
        ),
        **overrides,
    )


def reference(mailbox="INBOX", uid=42):
    return encode_source_reference(mailbox, 77, uid)


def reply(**overrides):
    return replace(
        EmailReplyDraftRequest(reference(), "Reply <safe> & sound", None, None),
        **overrides,
    )


def source_message(
    *, body="Original first line.\nSecond line.", html=False, attachment=False
):
    message = EmailMessage()
    for key, value in {
        "From": "Alice <alice@example.com>",
        "Reply-To": "reply@example.com",
        "To": "me@example.com, bob@example.com",
        "Cc": "carol@example.com, BOB@example.com",
        "Bcc": "hidden@example.com",
        "Subject": "Project update",
        "Message-ID": "<parent@example.com>",
        "References": "<root@example.com>",
        "Date": "Fri, 24 Jul 2026 11:20:30 +0300",
    }.items():
        message[key] = value
    message.set_content(body, subtype="html" if html else "plain")
    if attachment:
        message.add_attachment(
            b"payload",
            maintype="application",
            subtype="octet-stream",
            filename="file.bin",
        )
    return message.as_bytes(policy=policy.SMTP)


def signature():
    return EmailSignature(
        "Jane Example",
        '<b>Jane Example</b><img src="cid:logo">',
        EmailInlineImage(b"image", "logo.png", "image/png", "logo"),
        ("Signature notice",),
    )


def parsed_draft(client):
    return BytesParser(policy=policy.default).parsebytes(client.appended[-1][1])


def test_settings_require_explicit_decrypted_credentials(monkeypatch):
    monkeypatch.setenv("PROTON_BRIDGE_PASSWORD_SECRET", "unused")
    configured = settings(certificate_sha256="a2:" * 31 + "a2")
    assert configured.password.get_secret_value() == "bridge-password"
    assert configured.certificate_sha256 == "a2" * 32
    assert all(
        value not in repr(configured) for value in ("bridge-user", "bridge-password")
    )
    for values in (
        configured.model_dump(exclude={"password"}),
        configured.model_dump() | {"password": "roboz:v1:opaque"},
    ):
        with pytest.raises(ValidationError) as error:
            ProtonBridgeSettings.model_validate(values)
        assert "roboz:v1:opaque" not in str(error.value)


@pytest.mark.parametrize("signed", [False, True])
def test_draft_headers_signature_and_attachments(client, tmp_path, signed):
    path = tmp_path / "notes.txt"
    path.write_text("attached notes")
    result = service(client, signature() if signed else None).create_draft(
        draft(attachments=(EmailDraftAttachment(str(path), path.name),)),
        is_cancelled=active,
    )
    message = parsed_draft(client)
    assert (
        result.draft_id == "imap-uid:99" and result.account_address == "me@example.com"
    )
    assert result.warnings == (("Signature notice",) if signed else ())
    assert client.appended[0][0] == "My Drafts" and client.appended[0][2] == [
        b"\\Draft"
    ]
    for header, value in {
        "From": "me@example.com",
        "To": "person@example.com",
        "Cc": "copy@example.com",
        "Bcc": "hidden@example.com",
        "Reply-To": "reply@example.com",
        "X-Roboz-Request-Id": "request-1",
    }.items():
        assert message[header] == value
    assert "Draft body" in message.get_body(("plain",)).get_content()
    assert ("Jane Example" in message.get_body(("plain",)).get_content()) is signed
    assert (message.get_body(("html",)) is not None) is signed
    if signed:
        assert any(part["Content-ID"] == "<logo>" for part in message.walk())
    attached = list(message.iter_attachments())
    assert attached[-1].get_payload(decode=True) == b"attached notes"
    assert client.closed


def test_unreadable_attachment_never_appends(client, tmp_path):
    with pytest.raises(EmailProviderError, match="Unable to read attachment"):
        service(client).create_draft(
            draft(
                attachments=(
                    EmailDraftAttachment(str(tmp_path / "missing"), "missing"),
                )
            ),
            is_cancelled=active,
        )
    assert not client.appended and client.closed


@pytest.mark.parametrize(
    "existing, reused",
    [("request-1", True), ("request-10", False), ("REQUEST-1", False)],
)
def test_deduplication_requires_exact_header_match(client, existing, reused):
    client.messages[42] = f"X-Roboz-Request-Id: {existing}\r\n\r\n".encode()
    result = service(client).create_draft(draft(), is_cancelled=active)
    assert result.draft_id == ("imap-uid:42" if reused else "imap-uid:99")
    assert bool(client.appended) is not reused


@pytest.mark.parametrize("lost_response", [False, True])
def test_uncertain_append_outcome_is_never_retried(client, lost_response):
    client.append_response = b"APPEND completed"
    if lost_response:
        client.append_error = IMAP4.abort("socket closed")
        with pytest.raises(EmailProviderError, match="outcome is unknown"):
            service(client).create_draft(draft(), is_cancelled=active)
    else:
        result = service(client).create_draft(draft(), is_cancelled=active)
        assert "verify the Drafts folder before retrying" in result.warnings[0]
    assert len(client.appended) == 1 and client.closed


def test_cancellation_during_duplicate_check_prevents_append(client):
    cancelled = Event()
    client.after_search = cancelled.set
    with pytest.raises(EmailProviderError, match="cancelled"):
        service(client).create_draft(draft(), is_cancelled=cancelled.is_set)
    assert not client.appended and client.closed


@pytest.mark.parametrize("cancel", [False, True])
def test_abandoned_call_prevents_late_worker_append(client, cancel):
    release, done = Event(), Event()
    pipe = EventPipe()

    def block_search():
        if cancel:
            pipe.cancel()
        assert release.wait(3)

    client.after_search = block_search
    provider = service(client)

    def operation(is_cancelled):
        try:
            return provider.create_draft(draft(), is_cancelled=is_cancelled)
        finally:
            done.set()

    try:
        with pytest.raises(
            ExternalCallCancelledError if cancel else ExternalCallTimeoutError
        ):
            run_email_call(
                EmailContext(
                    service=provider,
                    timeout_s=3 if cancel else 0.05,
                    is_cancelled=active,
                    pipe=pipe,
                ),
                label="test-draft-timeout",
                cancelled_message="cancelled",
                operation=operation,
            )
    finally:
        release.set()
    assert done.wait(3) and not client.appended and client.closed


@pytest.mark.parametrize("failure", ["login", "logout"])
def test_failures_close_connection_and_hide_credentials(client, failure):
    setattr(client, failure + "_error", IMAP4.abort("bridge-password"))
    if failure == "login":
        with pytest.raises(EmailProviderError) as error:
            service(client).probe()
        assert "bridge-password" not in str(error.value)
    else:
        assert service(client).probe()["drafts_mailbox"] == "My Drafts"
    assert client.closed and not client.appended


def test_search_orders_by_received_date_then_uid_and_bounds_preview(client):
    client.messages = {uid: source_message(body="x" * 400) for uid in (1, 2, 99)}
    client.dates = {1: NOW, 2: NOW, 99: NOW - timedelta(days=1)}
    client.uids = [99, 1, 2, 100]  # A vanished message is skipped.
    client.seen = {2}
    results = service(client).search_messages(
        EmailSearchRequest(subject_contains='héllo "world"', since=date(2026, 1, 1)),
        is_cancelled=active,
    )
    assert [result.source_message_ref for result in results] == [
        reference(uid=uid) for uid in (2, 1, 99)
    ]
    assert results[0].is_read and not results[1].is_read
    assert all(
        result.replyable and len(result.preview_text) == 160 for result in results
    )
    assert client.searches == [
        (["SUBJECT", 'héllo "world"', "SINCE", date(2026, 1, 1)], "UTF-8")
    ]
    assert client.seen == {2} and client.selected == [("INBOX", True)]


def test_search_batches_metadata_and_rejects_excessive_matches(client):
    client.uids = list(range(1, 502))
    service(client).search_messages(EmailSearchRequest(), is_cancelled=active)
    batches = [
        uids for uids, fields in client.fetches if fields == ("INTERNALDATE", "FLAGS")
    ]
    assert [len(batch) for batch in batches] == [500, 1]
    client.uids = list(range(1, 10002))
    with pytest.raises(EmailProviderError, match="Too many"):
        service(client).search_messages(EmailSearchRequest(), is_cancelled=active)


@pytest.mark.parametrize("failure", ["provider", "date", "headers"])
def test_search_does_not_hide_provider_or_metadata_failures(client, failure):
    if failure == "provider":
        client.fetch_error = IMAP4.error("rejected")
    elif failure == "date":
        client.dates.clear()
    else:
        client.messages[42] = b"Subject: " + b"x" * 65536 + b"\r\n\r\n"
    with pytest.raises(EmailProviderError):
        service(client).search_messages(EmailSearchRequest(), is_cancelled=active)


@pytest.mark.parametrize(
    "mailbox, physical",
    [
        (EmailMailbox.INBOX, "INBOX"),
        (EmailMailbox.DRAFTS, "My Drafts"),
        (EmailMailbox.SENT, "Sent"),
    ],
)
def test_reads_and_attachment_download_respect_mailbox_state(client, mailbox, physical):
    client.messages[42] = source_message(attachment=True)
    provider = service(client)
    result = provider.read_message(mailbox, reference(physical), is_cancelled=active)
    assert result.body_text == "Original first line.\nSecond line."
    assert result.timestamp == NOW
    assert bool(client.seen) is (mailbox is EmailMailbox.INBOX)
    assert client.selected == [(physical, mailbox is not EmailMailbox.INBOX)]
    attachment = provider.download_attachment(
        result.attachments[0].attachment_ref, is_cancelled=active
    )
    assert attachment.data == b"payload" and attachment.filename == "file.bin"
    assert client.selected[-1] == (physical, True)


@pytest.mark.parametrize(
    "ref, validity",
    [("invalid", 77), (reference("my drafts"), 77), (reference("My Drafts"), 78)],
)
def test_invalid_cross_mailbox_or_stale_references_are_rejected(client, ref, validity):
    client.validity = validity
    with pytest.raises(EmailProviderError):
        service(client).read_message(EmailMailbox.DRAFTS, ref, is_cancelled=active)
    assert not client.seen


def test_cancelled_read_does_not_mark_seen(client):
    cancelled = Event()
    client.after_fetch = cancelled.set
    with pytest.raises(EmailProviderError, match="cancelled"):
        service(client).read_message(
            EmailMailbox.INBOX, reference(), is_cancelled=cancelled.is_set
        )
    assert not client.seen


def test_oversize_read_does_not_mark_seen(client):
    client.messages[42] = b"x" * 25_000_001
    with pytest.raises(EmailProviderError, match="25 MB"):
        service(client).read_message(
            EmailMailbox.INBOX, reference(), is_cancelled=active
        )
    assert not client.seen


@pytest.mark.parametrize(
    "signed, reply_all, quote",
    [(False, True, True), (True, True, True), (False, False, False)],
)
def test_threaded_replies_preserve_recipients_and_quote_options(
    client, signed, reply_all, quote
):
    result = service(client, signature() if signed else None).create_reply_draft(
        reply(reply_all=reply_all, include_quoted_original=quote), is_cancelled=active
    )
    message = parsed_draft(client)
    assert result.to == (
        ("reply@example.com", "bob@example.com")
        if reply_all
        else ("reply@example.com",)
    )
    assert result.cc == (("carol@example.com",) if reply_all else ())
    assert message["Bcc"] is None and result.subject == "Re: Project update"
    assert message["In-Reply-To"] == "<parent@example.com>"
    assert message["References"] == "<root@example.com> <parent@example.com>"
    plain = message.get_body(("plain",)).get_content()
    assert ("> Original first line." in plain) is quote
    assert result.quoted_original_included is quote
    if signed:
        html = message.get_body(("html",)).get_content()
        assert "Reply &lt;safe&gt; &amp; sound" in html and "protonmail_quote" in html
    assert not client.seen


@pytest.mark.parametrize(
    "body, html, expected",
    [
        (
            "<head><style>hidden</style></head><p>Hello <b>world</b></p><script>bad()</script>",
            True,
            "Hello world",
        ),
        ("x" * 100_001, False, "x" * 100_000),
        ("", False, None),
    ],
)
def test_body_extraction_and_reply_quote_warnings(client, body, html, expected):
    client.messages[42] = source_message(body=body, html=html)
    result = service(client).read_message(
        EmailMailbox.DRAFTS, reference("My Drafts"), is_cancelled=active
    )
    assert result.body_text == expected
    receipt = service(client).create_reply_draft(reply(), is_cancelled=active)
    assert bool(receipt.warnings) is (expected is None or len(body) > 100_000)


def test_body_excludes_text_from_an_attached_email(client):
    message = BytesParser(_class=EmailMessage, policy=policy.default).parsebytes(
        source_message(body="<p>Main message</p>", html=True)
    )
    forwarded = EmailMessage()
    forwarded.set_content("Attached email body")
    message.add_attachment(forwarded)
    client.messages[42] = message.as_bytes(policy=policy.SMTP)

    result = service(client).read_message(
        EmailMailbox.DRAFTS, reference("My Drafts"), is_cancelled=active
    )
    assert result.body_text == "Main message"


def test_reply_requires_threadable_source_and_uses_from_fallback(client):
    client.messages[42] = (
        b"From: alice@example.com\r\nSubject: Re: Already\r\nMessage-ID: invalid\r\n\r\n"
    )
    with pytest.raises(EmailProviderError, match="no valid Message-ID"):
        service(client).create_reply_draft(reply(), is_cancelled=active)
    client.messages[42] = client.messages[42].replace(
        b"Message-ID: invalid", b"Message-ID: <parent@example.com>"
    )
    result = service(client).create_reply_draft(reply(), is_cancelled=active)
    assert result.to == ("alice@example.com",) and result.subject == "Re: Already"


def test_decrypted_credentials_and_factory_dependency(tmp_path, monkeypatch, client):
    import os
    from roboz.endpoints import encrypt_env, load_secrets

    for name in ("PROTON_BRIDGE_USERNAME_SECRET", "PROTON_BRIDGE_PASSWORD_SECRET"):
        monkeypatch.delenv(name, raising=False)
    source = tmp_path / ".env"
    source.write_text(
        "PROTON_BRIDGE_USERNAME_SECRET=bridge-user\nPROTON_BRIDGE_PASSWORD_SECRET=bridge-password\n"
    )
    load_secrets(encrypt_env(source, password="test"), password="test")
    configured = settings()
    configured = ProtonBridgeSettings.model_validate(
        configured.model_dump()
        | {
            "username": os.environ["PROTON_BRIDGE_USERNAME_SECRET"],
            "password": os.environ["PROTON_BRIDGE_PASSWORD_SECRET"],
        }
    )
    provider = ProtonBridgeEmailService(
        configured, client_factory=lambda settings, context: client
    )
    tools = get_work_with_email(
        service=provider, base=tmp_path, default_verdict=ActionVerdict.deny
    )
    assert not client.selected and not client.closed
    assert any(
        resource is provider
        for tool in tools
        for resource in tool.external_dependencies()
    )
    assert provider.check() and client.closed
    assert "bridge-password" not in str(provider.redacted_metadata())
