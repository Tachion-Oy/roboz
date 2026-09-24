"""Verified IMAPClient sessions and bounded mailbox access."""

import hashlib
import re
import ssl
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from itertools import batched
from types import TracebackType
from typing import Protocol, Self, cast

# IMAPClient has no published stubs; its used API is typed at this boundary.
from imapclient import IMAPClient  # pyright: ignore[reportMissingTypeStubs]

from ..contracts import EmailMailbox, EmailProviderError
from .models import ProtonBridgeSettings, ProtonBridgeTlsMode
from .references import decode_source_reference

type FetchData = dict[int, dict[bytes, object]]


class _Client(Protocol):
    normalise_times: bool

    def login(self, username: str, password: str) -> object: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def socket(self) -> ssl.SSLSocket: ...
    def list_folders(self) -> list[tuple[tuple[bytes, ...], bytes, str]]: ...
    def select_folder(
        self, folder: str, readonly: bool = False
    ) -> dict[bytes, object]: ...
    def search(self, criteria: Sequence[str | date], charset: str) -> list[int]: ...
    def fetch(self, messages: Sequence[int], data: Sequence[str]) -> FetchData: ...
    def append(self, folder: str, msg: bytes, flags: Sequence[bytes]) -> bytes: ...
    def add_flags(
        self, messages: Sequence[int], flags: Sequence[bytes], silent: bool
    ) -> object: ...


type ClientFactory = Callable[[ProtonBridgeSettings, ssl.SSLContext], _Client]

HEADER_LIMIT = 65_536
MESSAGE_LIMIT = 25_000_000
SEARCH_LIMIT = 10_000
REQUEST_ID_HEADER = "X-Roboz-Request-Id"


class MessageMissing(EmailProviderError):
    """A searched message disappeared before its content could be fetched."""


def default_client_factory(
    settings: ProtonBridgeSettings, context: ssl.SSLContext
) -> _Client:
    """Connect using the configured TLS mode; never fall back to plaintext."""
    client = IMAPClient(
        settings.imap_host,
        port=settings.imap_port,
        ssl=settings.tls_mode is ProtonBridgeTlsMode.SSL,
        ssl_context=context,
        timeout=settings.timeout_s,
    )
    try:
        if settings.tls_mode is ProtonBridgeTlsMode.STARTTLS:
            client.starttls(context)
    except BaseException:
        try:
            client.shutdown()
        except (OSError, IMAPClient.Error):
            pass
        raise
    return cast(_Client, client)


@contextmanager
def open_session(
    settings: ProtonBridgeSettings,
    factory: ClientFactory,
    is_cancelled: Callable[[], bool],
) -> Iterator["Session"]:
    """Verify trust before login and close the connection even when logout fails."""
    context = ssl.create_default_context(cafile=settings.ca_file)
    if settings.certificate_sha256 and settings.ca_file is None:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        if is_cancelled():
            raise EmailProviderError("email operation was cancelled")
        with factory(settings, context) as client:
            client.normalise_times = False  # Keep timezone-aware INTERNALDATE values.
            _verify_certificate(client, settings.certificate_sha256)
            session = Session(client, is_cancelled)
            session.check()
            client.login(
                settings.username.get_secret_value(),
                settings.password.get_secret_value(),
            )
            yield session
    except IMAPClient.Error as exc:
        raise EmailProviderError(
            "Proton Mail Bridge rejected the IMAP operation"
        ) from exc
    except OSError as exc:
        raise EmailProviderError(
            "Unable to communicate with Proton Mail Bridge"
        ) from exc


def _verify_certificate(client: _Client, fingerprint: str | None) -> None:
    if fingerprint is None:
        return
    # Read certificate metadata only; never read/write IMAP bytes via the socket.
    certificate = client.socket().getpeercert(binary_form=True)
    if not certificate or hashlib.sha256(certificate).hexdigest() != fingerprint:
        raise EmailProviderError(
            "Proton Mail Bridge certificate fingerprint did not match"
        )


class Session:
    """Own one connection, cancellation predicate, and discovered mailbox names."""

    def __init__(self, client: _Client, is_cancelled: Callable[[], bool]) -> None:
        """Keep operation state local so concurrent calls share no selected mailbox."""
        self._client = client
        self.is_cancelled = is_cancelled
        self._folders: list[tuple[tuple[bytes, ...], bytes, str]] | None = None

    def check(self) -> None:
        """Stop before the next command if the caller abandoned this operation."""
        if self.is_cancelled():
            raise EmailProviderError("email operation was cancelled")

    def mailbox(self, logical: EmailMailbox) -> str:
        """Resolve special-use flags without guessing localized mailbox names."""
        if logical is EmailMailbox.INBOX:
            return "INBOX"
        if self._folders is None:
            self.check()
            self._folders = self._client.list_folders()
        flag = b"\\Drafts" if logical is EmailMailbox.DRAFTS else b"\\Sent"
        matches = [
            name
            for flags, _, name in self._folders
            if flag.lower() in {f.lower() for f in flags}
        ]
        if len(matches) != 1:
            raise EmailProviderError(
                f"Email provider did not expose exactly one {logical.value.title()} mailbox"
            )
        return matches[0]

    def select(self, mailbox: str, *, readonly: bool = True) -> int:
        """Select a canonical mailbox and validate its UIDVALIDITY."""
        self.check()
        validity = self._client.select_folder(mailbox, readonly=readonly).get(
            b"UIDVALIDITY"
        )
        if not isinstance(validity, int) or validity <= 0:
            raise EmailProviderError(
                "Email provider returned an invalid mailbox UIDVALIDITY"
            )
        return validity

    def source(
        self, reference: str, logical: EmailMailbox | None, *, readonly: bool = True
    ) -> int:
        """Select only an allowed mailbox with matching identity and UIDVALIDITY."""
        decoded = decode_source_reference(reference)
        candidates = (logical,) if logical is not None else tuple(EmailMailbox)
        for candidate in candidates:
            name = self.mailbox(candidate)
            if decoded.mailbox == name or (
                name == "INBOX" and decoded.mailbox.upper() == "INBOX"
            ):
                if self.select(name, readonly=readonly) != decoded.uid_validity:
                    raise EmailProviderError(
                        "email source is stale; search email again"
                    )
                return decoded.uid
        raise EmailProviderError(
            "email source does not belong to the requested mailbox"
        )

    def search(self, criteria: Sequence[str | date]) -> list[int]:
        """Let IMAPClient quote criteria and encode Unicode literals."""
        self.check()
        if any(
            isinstance(term, str) and any(c in term for c in "\r\n\x00")
            for term in criteria
        ):
            raise EmailProviderError("email search contains invalid control characters")
        uids: list[int] = self._client.search(criteria, charset="UTF-8")
        if len(uids) > SEARCH_LIMIT:
            raise EmailProviderError("Too many emails matched; add more search filters")
        return uids

    def store_draft(
        self, message: bytes, request_id: str | None
    ) -> tuple[str, tuple[str, ...]]:
        """Reuse an exact request ID or append once, reporting uncertain outcomes."""
        mailbox = self.mailbox(EmailMailbox.DRAFTS)
        if request_id:
            self.select(mailbox)
            for uid in reversed(self.search(["HEADER", REQUEST_ID_HEADER, request_id])):
                try:
                    headers = self.headers(uid)
                except MessageMissing:
                    continue
                if headers.get_all(REQUEST_ID_HEADER, []) == [request_id]:
                    return f"imap-uid:{uid}", (
                        "Reused an existing draft for this request id.",
                    )
        self.check()  # The duplicate search may have outlived the caller.
        try:
            response: bytes = self._client.append(mailbox, message, flags=[b"\\Draft"])
        except (OSError, IMAPClient.AbortError) as exc:
            raise EmailProviderError(
                "Draft creation outcome is unknown; inspect Drafts before retrying"
            ) from exc
        match = re.search(rb"\bAPPENDUID [1-9][0-9]* ([1-9][0-9]*)\b", response)
        if match:
            return f"imap-uid:{int(match[1])}", ()
        return f"imap:{mailbox}:unknown", (
            "The provider did not return a draft UID; verify the Drafts folder before retrying.",
        )

    def search_newest(
        self, criteria: Sequence[str | date]
    ) -> list[tuple[datetime, int, bool]]:
        """Order matching UIDs by reception date, using UID to break ties."""
        uids = self.search(criteria)
        ordered: list[tuple[datetime, int, bool]] = []
        for batch in batched(uids, 500):
            for uid, fields in self._fetch(batch, ["INTERNALDATE", "FLAGS"]).items():
                timestamp, flags = fields.get(b"INTERNALDATE"), fields.get(b"FLAGS")
                if not isinstance(timestamp, datetime) or not isinstance(flags, tuple):
                    raise EmailProviderError(
                        "Email provider returned invalid search metadata"
                    )
                ordered.append((timestamp, uid, b"\\Seen" in flags))
        return sorted(ordered, reverse=True)

    def mark_seen(self, uid: int) -> None:
        """Mark a successfully parsed Inbox message read unless the caller cancelled."""
        self.check()
        self._client.add_flags([uid], [b"\\Seen"], silent=True)

    def headers(self, uid: int) -> EmailMessage:
        """Fetch bounded headers without opening the message body."""
        return self.message(uid, section="HEADER", limit=HEADER_LIMIT)[0]

    def _fetch(self, uids: Sequence[int], fields: Sequence[str]) -> FetchData:
        """Confine the library's heterogeneous response values to this boundary."""
        self.check()
        return self._client.fetch(uids, fields)

    def message(
        self,
        uid: int,
        *,
        section: str = "",
        limit: int = MESSAGE_LIMIT,
        truncate: bool = False,
    ) -> tuple[EmailMessage, datetime | None, bool]:
        """Fetch at most limit+1 bytes; reject oversize input unless truncation is requested."""
        fields = self._fetch(
            [uid], ["INTERNALDATE", f"BODY.PEEK[{section}]<0.{limit + 1}>"]
        ).get(uid, {})
        raw = fields.get(f"BODY[{section}]<0>".encode())
        if raw is None:
            raise MessageMissing("email source no longer exists")
        if not isinstance(raw, bytes):
            raise EmailProviderError("Email provider returned invalid message content")
        truncated = len(raw) > limit
        if truncated and not truncate:
            raise EmailProviderError(
                "email source headers are too large"
                if section
                else "email is larger than 25 MB"
            )
        timestamp = fields.get(b"INTERNALDATE")
        return (
            BytesParser(_class=EmailMessage, policy=policy.default).parsebytes(
                raw[:limit], headersonly=bool(section)
            ),
            timestamp if isinstance(timestamp, datetime) else None,
            truncated,
        )
