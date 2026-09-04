"""Agent-facing inputs for the five email tools."""

from datetime import date
from typing import Literal

from roboz.models import Empty
from pydantic import ConfigDict, Field


class CreateEmailDraft(Empty):
    to: list[str] = Field(..., min_length=1, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    bcc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(..., min_length=1, max_length=998)
    body_text: str = Field(..., min_length=1, max_length=100_000)
    from_address: str | None = None
    reply_to: str | None = None
    client_request_id: str | None = Field(default=None, max_length=128)
    attachment_paths: list[str] = Field(default_factory=list, max_length=10)
    model_config = ConfigDict(extra="forbid")


class SearchEmail(Empty):
    mailbox: Literal["inbox", "drafts", "sent"]
    from_address: str | None = Field(default=None, max_length=320)
    to_address: str | None = Field(default=None, max_length=320)
    subject_contains: str | None = Field(default=None, max_length=500)
    text_contains: str | None = Field(default=None, max_length=500)
    since: date | None = None
    before: date | None = None
    limit: int = Field(default=10, ge=1, le=20)
    model_config = ConfigDict(extra="forbid")


class ReadEmail(Empty):
    mailbox: Literal["inbox", "drafts", "sent"]
    source_message_ref: str = Field(..., min_length=1, max_length=2_048)
    model_config = ConfigDict(extra="forbid")


class DownloadEmailAttachment(Empty):
    attachment_ref: str = Field(..., min_length=1, max_length=4_096)
    destination_path: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


class CreateReplyDraft(Empty):
    source_message_ref: str = Field(..., min_length=1, max_length=2_048)
    body_text: str = Field(..., min_length=1, max_length=100_000)
    include_quoted_original: bool = True
    reply_all: bool = True
    from_address: str | None = None
    client_request_id: str | None = Field(default=None, max_length=128)
    attachment_paths: list[str] = Field(default_factory=list, max_length=10)
    model_config = ConfigDict(extra="forbid")
