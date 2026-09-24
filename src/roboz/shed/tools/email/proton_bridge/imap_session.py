"""Authenticated Proton Bridge IMAP session lifecycle."""

from __future__ import annotations

import hashlib
import imaplib
import ssl
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from ..contracts import EmailProviderError
from .imap_codec import require_ok
from .models import ProtonBridgeSettings, ProtonBridgeTlsMode

ImapFactory = Callable[[ProtonBridgeSettings, ssl.SSLContext], imaplib.IMAP4]


def default_imap_factory(
    settings: ProtonBridgeSettings, context: ssl.SSLContext
) -> imaplib.IMAP4:
    """Create the configured SSL or STARTTLS IMAP connection."""
    if settings.tls_mode is ProtonBridgeTlsMode.SSL:
        return imaplib.IMAP4_SSL(
            settings.imap_host,
            settings.imap_port,
            ssl_context=context,
            timeout=settings.timeout_s,
        )
    connection = imaplib.IMAP4(
        settings.imap_host,
        settings.imap_port,
        timeout=settings.timeout_s,
    )
    try:
        connection.starttls(ssl_context=context)
    except BaseException:
        try:
            connection.shutdown()
        except (OSError, imaplib.IMAP4.error):
            pass
        raise
    return connection


class ImapSessionProvider:
    """Open authenticated sessions with configured TLS verification."""

    def __init__(
        self,
        settings: ProtonBridgeSettings,
        *,
        imap_factory: ImapFactory = default_imap_factory,
    ) -> None:
        """Store caller-provided settings and the injectable IMAP transport."""
        self.settings = settings
        self._imap_factory = imap_factory

    @contextmanager
    def open(self) -> Generator[Any]:
        """Yield an authenticated session and always log out afterward."""
        connection: Any | None = None
        try:
            active_connection = self._connect()
            connection = active_connection
            status, _ = active_connection.login(
                self.settings.username.get_secret_value(),
                self.settings.password.get_secret_value(),
            )
            require_ok(status, "Proton Mail Bridge rejected the configured credentials")
            yield active_connection
        except EmailProviderError:
            raise
        except imaplib.IMAP4.error as exc:
            raise EmailProviderError(
                "Proton Mail Bridge rejected the IMAP operation"
            ) from exc
        except (OSError, ssl.SSLError) as exc:
            raise EmailProviderError(
                "Unable to communicate with Proton Mail Bridge"
            ) from exc
        finally:
            if connection is not None:
                _close_connection(connection)

    def _connect(self) -> imaplib.IMAP4:
        context = self._ssl_context()
        connection = self._imap_factory(self.settings, context)
        fingerprint = self.settings.certificate_sha256
        if fingerprint is None:
            return connection
        try:
            socket = getattr(connection, "sock", None)
            certificate = (
                socket.getpeercert(binary_form=True) if socket is not None else None
            )
            if not certificate:
                raise EmailProviderError(
                    "Could not read the Proton Mail Bridge certificate"
                )
            if hashlib.sha256(certificate).hexdigest() != fingerprint:
                raise EmailProviderError(
                    "Proton Mail Bridge certificate fingerprint did not match"
                )
        except BaseException:
            _close_connection(connection)
            raise
        return connection

    def _ssl_context(self) -> ssl.SSLContext:
        if self.settings.ca_file is not None:
            return ssl.create_default_context(cafile=str(self.settings.ca_file))
        if self.settings.certificate_sha256 is None:
            return ssl.create_default_context()
        # The exact DER certificate is verified in _connect.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context


def ensure_not_cancelled(is_cancelled: Callable[[], bool]) -> None:
    """Stop before an external operation when cancellation was requested."""
    if is_cancelled():
        raise EmailProviderError("email operation was cancelled")


def _close_connection(connection: imaplib.IMAP4) -> None:
    """Best-effort logout without hiding the original provider failure."""
    try:
        connection.logout()
    except (imaplib.IMAP4.error, OSError):
        pass
