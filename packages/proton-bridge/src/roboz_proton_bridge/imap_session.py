"""Authenticated Proton Bridge IMAP session lifecycle."""

from __future__ import annotations

import hashlib
import imaplib
import ssl
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from roboshed.tools.email.contracts import EmailProviderError
from .imap_codec import require_ok
from .models import ProtonBridgeSettings, ProtonBridgeTlsMode
from .protocol import IMAP_TIMEOUT_SECONDS

ImapFactory = Callable[[ProtonBridgeSettings, ssl.SSLContext], Any]


def default_imap_factory(
    settings: ProtonBridgeSettings, context: ssl.SSLContext
) -> imaplib.IMAP4:
    """Create the configured SSL or STARTTLS IMAP connection."""
    if settings.tls_mode is ProtonBridgeTlsMode.SSL:
        return imaplib.IMAP4_SSL(
            settings.imap_host,
            settings.imap_port,
            ssl_context=context,
            timeout=IMAP_TIMEOUT_SECONDS,
        )
    connection = imaplib.IMAP4(
        settings.imap_host,
        settings.imap_port,
        timeout=IMAP_TIMEOUT_SECONDS,
    )
    connection.starttls(ssl_context=context)
    return connection


class ImapSessionProvider:
    """Open authenticated sessions with configured TLS verification."""

    def __init__(
        self,
        settings: ProtonBridgeSettings,
        *,
        imap_factory: ImapFactory = default_imap_factory,
    ) -> None:
        """Initialize a provider with settings and an injectable IMAP factory."""
        self.settings = settings
        self._imap_factory = imap_factory

    @contextmanager
    def open(self) -> Generator[Any]:
        """Open, authenticate, yield, and safely close one IMAP session."""
        connection: Any | None = None
        try:
            active_connection = self._connect()
            connection = active_connection
            status, _ = active_connection.login(
                self.settings.username,
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
                try:
                    connection.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass

    def _connect(self) -> Any:
        context = self._ssl_context()
        connection = self._imap_factory(self.settings, context)
        fingerprint = self.settings.certificate_sha256
        if fingerprint is None:
            return connection
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
    """Raise a safe provider error when cancellation is requested."""
    if is_cancelled():
        raise EmailProviderError("email operation was cancelled")
