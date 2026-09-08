"""Draft persistence and request-id deduplication."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from roboshed.tools.email.contracts import EmailMailbox
from .imap_codec import append_uid, require_ok, uid_identifier
from .imap_session import ImapSessionProvider, ensure_not_cancelled
from .mailbox import mailbox_for
from .protocol import EmailHeader, ImapSearchKey, ImapSystemFlag, ImapUidCommand


def store_draft(
    sessions: ImapSessionProvider,
    message: bytes,
    *,
    request_id: str | None,
    is_cancelled: Callable[[], bool],
) -> tuple[str, tuple[str, ...]]:
    """Store a draft and return its identifier and warnings.

    Reuse an existing draft when a nonempty request ID matches one in the
    Drafts mailbox. Without a request ID, each call appends a new draft.
    The lookup and append are not atomic, so concurrent calls can still create
    duplicates even with the same request ID.
    """
    ensure_not_cancelled(is_cancelled)
    with sessions.open() as connection:
        drafts_mailbox = mailbox_for(connection, EmailMailbox.DRAFTS)
        ensure_not_cancelled(is_cancelled)
        if request_id:
            existing = _find_by_request_id(connection, drafts_mailbox, request_id)
            if existing is not None:
                return existing, ("Reused an existing draft for this request id.",)
        status, response = connection.append(
            drafts_mailbox, f"({ImapSystemFlag.DRAFT})", None, message
        )
        require_ok(status, "Proton Mail Bridge did not accept the draft")
        draft_id = append_uid(response)
        if draft_id is None:
            return (
                f"imap:{drafts_mailbox}:unknown",
                (
                    "The provider did not return a draft UID; verify the Drafts folder before retrying.",
                ),
            )
        ensure_not_cancelled(is_cancelled)
        return draft_id, ()


def probe_mailboxes(sessions: ImapSessionProvider) -> dict[str, object]:
    """Probe authenticated access to the configured Drafts and Sent mailboxes."""
    settings = sessions.settings
    with sessions.open() as connection:
        return {
            "provider": "proton_bridge",
            "imap_host": settings.imap_host,
            "imap_port": settings.imap_port,
            "tls_mode": settings.tls_mode,
            "drafts_mailbox": mailbox_for(connection, EmailMailbox.DRAFTS),
            "sent_mailbox": mailbox_for(connection, EmailMailbox.SENT),
        }


def _find_by_request_id(connection: Any, mailbox: str, request_id: str) -> str | None:
    status, _ = connection.select(mailbox, readonly=True)
    require_ok(status, "Could not open the Proton Mail Bridge Drafts mailbox")
    status, response = connection.uid(
        ImapUidCommand.SEARCH,
        None,
        ImapSearchKey.HEADER,
        EmailHeader.REQUEST_ID,
        request_id,
    )
    require_ok(status, "Could not check for an existing email draft")
    raw = response[0] if response else b""
    uids = raw.split() if isinstance(raw, bytes) else []
    return uid_identifier(uids[-1]) if uids else None
