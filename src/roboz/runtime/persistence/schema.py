"""Conversation persistence schema: run document and message rows."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from roboz.models import Message, MessageKind, Role
from roboz.runtime.observability import (
    RuntimeEventCategory,
    RuntimeEventKind,
    RuntimeEventLevel,
)
from roboz.models.truncation import TruncationSpec


class RunStatus(StrEnum):
    """Persisted lifecycle state of an agent run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunMetadata(BaseModel):
    """Optional host-provided metadata for a run."""

    model_config = ConfigDict(extra="forbid")

    environment: str | None = None
    agent_description: str | None = None
    tags: list[str] = Field(default_factory=list)
    custom: dict[str, Any] = Field(default_factory=dict)


class LoggedMessageRow(BaseModel):
    """One persisted message line (tool output serialized as Message)."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    sequence: int
    created_at: str
    role: Role
    content: str
    truncation: TruncationSpec
    message_kind: MessageKind | None = None
    endpoint: str | None = None
    model: str | None = None
    token_input: int | None = None
    token_output: int | None = None


class RuntimeEventRow(BaseModel):
    """One persisted runtime observation, separate from conversation messages."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    sequence: int
    created_at: str
    category: RuntimeEventCategory
    kind: RuntimeEventKind
    level: RuntimeEventLevel
    message: str
    agent_name: str
    data: dict[str, Any] = Field(default_factory=dict)


class ConversationRun(BaseModel):
    """Full JSON document for one agent invoke (one file on disk)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = 3
    conversation_id: str
    agent_name: str
    parent_conversation_id: str | None = None
    started_at: str
    ended_at: str | None = None
    status: RunStatus = RunStatus.RUNNING
    metadata: RunMetadata = Field(default_factory=RunMetadata)
    messages: list[LoggedMessageRow] = Field(default_factory=list)
    runtime_events: list[RuntimeEventRow] = Field(default_factory=list)


def utc_iso_z(dt: datetime) -> str:
    """Format a datetime as a millisecond UTC timestamp ending in ``Z``."""
    u = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return (
        u.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def message_to_logged_row(
    message: Message,
    *,
    message_id: str,
    sequence: int,
    created_at: datetime,
) -> LoggedMessageRow:
    """Build a persisted row from an in-memory Message (UTC `created_at`)."""
    return LoggedMessageRow(
        message_id=message_id,
        sequence=sequence,
        created_at=utc_iso_z(created_at),
        role=message.role,
        content=message.content,
        truncation=message.truncation,
        message_kind=message.message_kind,
        endpoint=message.endpoint,
        model=message.model,
        token_input=message.token_input,
        token_output=message.token_output,
    )


def logged_row_to_message(row: LoggedMessageRow) -> Message:
    """Rebuild an in-memory Message from one persisted row."""
    return Message(
        role=row.role,
        content=row.content,
        truncation=row.truncation,
        message_kind=row.message_kind,
        endpoint=row.endpoint,
        model=row.model,
        token_input=row.token_input,
        token_output=row.token_output,
    )


def runtime_event_to_logged_row(
    event,
    *,
    event_id: str,
    created_at: datetime,
) -> RuntimeEventRow:
    """Build a persisted row from an in-memory RuntimeEvent."""
    return RuntimeEventRow(
        event_id=event_id,
        sequence=event.sequence,
        created_at=utc_iso_z(created_at),
        category=event.category,
        kind=event.kind,
        level=event.level,
        message=event.message,
        agent_name=event.agent_name,
        data=event.data or {},
    )
