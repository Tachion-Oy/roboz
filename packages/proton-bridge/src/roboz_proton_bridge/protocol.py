"""Typed email and IMAP protocol vocabulary for Proton Bridge."""

from enum import StrEnum
from typing import Final

IMAP_TIMEOUT_SECONDS: Final = 15
IMAP_UID_IDENTIFIER_PREFIX: Final = "imap-uid:"
IMAP_SOURCE_REFERENCE_PREFIX: Final = "proton-imap-message:v1:"
IMAP_ATTACHMENT_REFERENCE_PREFIX: Final = "proton-imap-attachment:v1:"
IMAP_INBOX_MAILBOX: Final = "INBOX"
IMAP_SEARCH_CHARSET_ARGUMENT: Final = "CHARSET"
IMAP_SEARCH_CHARSET: Final = "UTF-8"
MAX_FETCHED_HEADER_BYTES: Final = 65_536
MAX_QUOTED_MESSAGE_FETCH_BYTES: Final = 1_000_000
MAX_FULL_MESSAGE_BYTES: Final = 25_000_000
MAX_SEARCH_PREVIEW_FETCH_BYTES: Final = 16_384
MAX_SEARCH_PREVIEW_CHARS: Final = 160
MAX_QUOTED_BODY_CHARS: Final = 100_000
MAX_REPLY_RECIPIENTS: Final = 50
MAX_SEARCH_METADATA_CANDIDATES: Final = 10_000
SEARCH_METADATA_BATCH_SIZE: Final = 500


class EmailHeader(StrEnum):
    FROM = "From"
    TO = "To"
    CC = "Cc"
    BCC = "Bcc"
    DATE = "Date"
    SUBJECT = "Subject"
    REPLY_TO = "Reply-To"
    MESSAGE_ID = "Message-ID"
    IN_REPLY_TO = "In-Reply-To"
    REFERENCES = "References"
    REQUEST_ID = "X-Peffa-Request-Id"


class ImapResponseStatus(StrEnum):
    """Response statuses defined by the IMAP protocol."""

    OK = "OK"
    NO = "NO"
    BAD = "BAD"
    PREAUTH = "PREAUTH"
    BYE = "BYE"


class ImapResponseCode(StrEnum):
    APPEND_UID = "APPENDUID"


class ImapUidCommand(StrEnum):
    SEARCH = "SEARCH"
    FETCH = "FETCH"
    STORE = "STORE"


class ImapSearchKey(StrEnum):
    ALL = "ALL"
    BEFORE = "BEFORE"
    FROM = "FROM"
    HEADER = "HEADER"
    SINCE = "SINCE"
    SUBJECT = "SUBJECT"
    TEXT = "TEXT"
    TO = "TO"


class ImapSystemFlag(StrEnum):
    DRAFT = r"\Draft"
    SEEN = r"\Seen"


class ImapMailboxAttribute(StrEnum):
    DRAFTS = r"\Drafts"
    SENT = r"\Sent"
