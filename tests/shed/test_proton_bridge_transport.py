from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import re
import socket
import ssl
from threading import Thread

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from roboz.shed.tools.email import (
    EmailDraftRequest,
    EmailMailbox,
    EmailProviderError,
    EmailSearchRequest,
)
from roboz.shed.tools.email.proton_bridge import (
    ProtonBridgeEmailService,
    ProtonBridgeSettings,
)
from roboz.shed.tools.email.proton_bridge.references import encode_source_reference


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    directory = tmp_path_factory.mktemp("bridge-tls")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return (
        cert_path,
        key_path,
        hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest(),
    )


@contextmanager
def bridge(certificate, mode, *, reject_starttls=False):
    """A local scripted peer exercises real IMAPClient encoding, parsing and TLS."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*certificate[:2])
    commands, errors = [], []
    draft_folder = b'My "Drafts" &AOQ-'
    headers = (
        b"From: sender@example.com\r\nTo: me@example.com\r\n"
        b"Subject: Wire test\r\nMessage-ID: <wire@example.com>\r\n\r\n"
    )
    message = headers + b"Wire body\r\n"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)

    def serve():
        peer = stream = None
        try:
            peer, _ = listener.accept()
            peer.settimeout(3)
            if mode == "ssl":
                peer = context.wrap_socket(peer, server_side=True)
            stream = peer.makefile("rb")
            peer.sendall(b"* OK test Bridge\r\n")
            while line := stream.readline():
                commands.append(line)
                tag, command, *args = line.rstrip(b"\r\n").split(b" ", 2)
                if command == b"CAPABILITY":
                    peer.sendall(b"* CAPABILITY IMAP4rev1 STARTTLS UIDPLUS\r\n")
                elif command == b"STARTTLS":
                    peer.sendall(
                        tag
                        + (
                            b" NO rejected\r\n"
                            if reject_starttls
                            else b" OK upgrade\r\n"
                        )
                    )
                    if not reject_starttls:
                        stream.close()
                        peer = context.wrap_socket(peer, server_side=True)
                        stream = peer.makefile("rb")
                    continue
                elif command == b"LOGIN":
                    assert b"bridge-user" in line and b"bridge-password" in line
                elif command == b"LIST":
                    # Literal mailbox names cannot be parsed by the previous regex.
                    peer.sendall(
                        b'* LIST (\\Drafts) "/" {'
                        + str(len(draft_folder)).encode()
                        + b"}\r\n"
                        + draft_folder
                        + b'\r\n* LIST (\\Sent) "/" "Sent"\r\n'
                    )
                elif command in (b"EXAMINE", b"SELECT"):
                    if b"INBOX" not in line:
                        assert b'"My \\"Drafts\\" &AOQ-"' in line
                    peer.sendall(
                        b"* 1 EXISTS\r\n* 0 RECENT\r\n* FLAGS (\\Seen)\r\n* OK [UIDVALIDITY 77] valid\r\n"
                    )
                elif command == b"UID":
                    if args[0].startswith(b"SEARCH"):
                        literal = re.search(rb"\{(\d+)\}$", line.rstrip())
                        if literal:
                            peer.sendall(b"+ literal\r\n")
                            term = stream.read(int(literal[1]))
                            assert term == 'héllo "world"'.encode()
                            commands.append(term + stream.readline())
                        peer.sendall(b"* SEARCH 42\r\n")
                    elif args[0].startswith(b"FETCH"):
                        fields = b'UID 42 INTERNALDATE "24-Jul-2026 11:20:30 +0300" FLAGS (\\Seen)'
                        part = re.search(rb"BODY.PEEK\[(.*?)\]<0\.(\d+)>", line)
                        if part:
                            raw = headers if part[1] == b"HEADER" else message
                            raw = raw[: int(part[2])]
                            fields += (
                                b" BODY["
                                + part[1]
                                + b"]<0> {"
                                + str(len(raw)).encode()
                                + b"}\r\n"
                                + raw
                            )
                        peer.sendall(b"* 1 FETCH (" + fields + b")\r\n")
                    else:
                        assert args[0].startswith(b"STORE")
                elif command == b"APPEND":
                    assert b'"My \\"Drafts\\" &AOQ-"' in line and b"(\\Draft)" in line
                    length = int(re.search(rb"\{(\d+)\}", line)[1])
                    peer.sendall(b"+ append\r\n")
                    raw = stream.read(length)
                    assert b"Subject: Draft subject" in raw
                    assert stream.readline() == b"\r\n"
                    peer.sendall(tag + b" OK [APPENDUID 77 99] saved\r\n")
                    continue
                elif command == b"LOGOUT":
                    peer.sendall(b"* BYE logout\r\n" + tag + b" OK done\r\n")
                    return
                else:
                    raise AssertionError(line)
                peer.sendall(tag + b" OK done\r\n")
        except (ssl.SSLError, ConnectionError):
            # Expected when a client rejects the test certificate.
            pass
        except BaseException as exc:
            errors.append(exc)
        finally:
            if stream:
                stream.close()
            if peer:
                peer.close()

    thread = Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], commands
    finally:
        thread.join(6)
        listener.close()
        assert not thread.is_alive(), "scripted Bridge did not close"
        if errors:
            raise errors[0]


def configured(port, mode, **trust):
    return ProtonBridgeEmailService(
        ProtonBridgeSettings.model_validate(
            dict(
                imap_host="127.0.0.1",
                imap_port=port,
                tls_mode=mode,
                account_address="me@example.com",
                username="bridge-user",
                password="bridge-password",
                timeout_s=2,
                **trust,
            )
        )
    )


@pytest.mark.parametrize(
    "mode, operation", [("ssl", "search"), ("starttls", "draft"), ("starttls", "read")]
)
def test_real_client_handles_mailboxes_literals_and_parsed_fetches(
    certificate, mode, operation
):
    with bridge(certificate, mode) as (port, commands):
        provider = configured(port, mode, certificate_sha256=certificate[2])
        if operation == "search":
            (result,) = provider.search_messages(
                EmailSearchRequest(
                    mailbox=EmailMailbox.DRAFTS, subject_contains='héllo "world"'
                ),
                is_cancelled=lambda: False,
            )
            assert result.subject == "Wire test" and result.preview_text == "Wire body"
            assert result.is_read and result.timestamp.utcoffset() == timedelta(hours=3)
        elif operation == "draft":
            result = provider.create_draft(
                EmailDraftRequest(
                    ("recipient@example.com",),
                    (),
                    (),
                    "Draft subject",
                    "Draft body",
                    None,
                    None,
                    None,
                ),
                is_cancelled=lambda: False,
            )
            assert result.draft_id == "imap-uid:99"
        else:
            result = provider.read_message(
                EmailMailbox.INBOX,
                encode_source_reference("inbox", 77, 42),
                is_cancelled=lambda: False,
            )
            assert result.body_text == "Wire body" and any(
                b"UID STORE" in line for line in commands
            )
    assert any(b"LOGIN" in line for line in commands) and b"LOGOUT" in commands[-1]


@pytest.mark.parametrize("trust", ["ca", "untrusted", "wrong-pin"])
def test_real_tls_authenticates_only_after_verification(certificate, trust):
    options = (
        {"ca_file": certificate[0]}
        if trust == "ca"
        else {"certificate_sha256": "00" * 32}
        if trust == "wrong-pin"
        else {}
    )
    with bridge(certificate, "ssl") as (port, commands):
        provider = configured(port, "ssl", **options)
        if trust == "ca":
            assert provider.probe()["drafts_mailbox"] == 'My "Drafts" ä'
        else:
            with pytest.raises(EmailProviderError):
                provider.probe()
            assert not any(b"LOGIN" in line for line in commands)


def test_failed_starttls_closes_without_sending_credentials(certificate):
    with bridge(certificate, "starttls", reject_starttls=True) as (port, commands):
        with pytest.raises(EmailProviderError):
            configured(port, "starttls", certificate_sha256=certificate[2]).probe()
        assert not any(b"LOGIN" in line for line in commands)
