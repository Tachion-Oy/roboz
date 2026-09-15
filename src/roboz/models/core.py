"""Core Pydantic models for messages, values, actions, and stop results."""

from __future__ import annotations

from enum import StrEnum, auto
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from roboz.models.truncation import DEFAULT, TruncationSpec


class BaseNames(StrEnum):
    """Canonical field names shared by model and prompt machinery."""

    ACTION_FIELD = "action"
    RATIONALE_FIELD = "rationale"
    CALLER_FIELD = "caller"
    VALUE_FIELD = "value"
    KIND_FIELD = "kind"


class Role(StrEnum):
    """Conversation roles understood by the runtime."""

    SYSTEM = auto()
    ASSISTANT = auto()
    USER = auto()
    ERROR = auto()


class MessageKind(StrEnum):
    """Optional semantic classifications for persisted messages."""

    AUTO_LOAD_BANNER = "auto_load_banner"
    AUTO_LOADED_SKILL = "auto_loaded_skill"
    COMPACTED_CONTEXT = "compacted_context"
    INTERRUPTED_GENERATION = "interrupted_generation"
    LIBRARIAN_ACTIVITY = "librarian_activity"
    LIBRARIAN_ROUTINE = "librarian_routine"
    STARTUP_CONTEXT = "startup_context"
    SYSTEM_MESSAGE = "system_message"
    USER_NOTIFICATION = "user_notification"


BOOTSTRAP_MESSAGE_KINDS: Final[frozenset[MessageKind]] = frozenset(
    {
        MessageKind.SYSTEM_MESSAGE,
        MessageKind.STARTUP_CONTEXT,
        MessageKind.AUTO_LOAD_BANNER,
        MessageKind.AUTO_LOADED_SKILL,
    }
)


class AgentBaseModel(BaseModel):
    """Base metadata carried by values moving through an agent run."""

    truncation: TruncationSpec = Field(default=DEFAULT)
    message_kind: MessageKind | None = None
    endpoint: str | None = None
    model: str | None = None
    token_input: int | None = None
    token_output: int | None = None


class Empty(AgentBaseModel):
    """Base value with no required payload fields."""


class Stop(AgentBaseModel):
    """Signal successful agent termination with an optional final value."""

    value: str | None = Field(default=None)
    model_config = ConfigDict(extra="forbid")


class All(Empty):
    """Accept arbitrary payload fields for catch-all chain inputs."""

    model_config = ConfigDict(extra="allow")


class Int(Empty):
    """Carry one integer value between tools."""

    value: int = Field(...)
    model_config = ConfigDict(extra="forbid")


class Ints(Empty):
    """Carry one integer value between tools."""

    value: list[int] = Field(...)
    model_config = ConfigDict(extra="forbid")


class Str(Empty):
    """Carry one string value between tools."""

    value: str = Field(...)
    model_config = ConfigDict(extra="forbid")


class Location(Empty):
    """Carry one filesystem path between tools."""

    value: Path = Field(...)
    model_config = ConfigDict(extra="forbid")


class Strs(Empty):
    """Carry a list of strings between tools."""

    items: list[str] = Field(...)
    model_config = ConfigDict(extra="forbid")


class LocationStr(Empty):
    """Carry paired filesystem and text values between tools."""

    location: Path = Field(...)
    value: str = Field(...)
    model_config = ConfigDict(extra="forbid")


class StopLocation(Stop):
    """Signal termination while retaining a related filesystem path."""

    location: Path = Field(...)
    model_config = ConfigDict(extra="forbid")


class HashMaps(Empty):
    """Carry a list of mapping payloads between tools."""

    items: list[dict] = Field(...)
    model_config = ConfigDict(extra="forbid")


class Invoke(AgentBaseModel):
    """Request invocation of a named tool with model-supplied rationale."""

    action: str = Field(...)
    rationale: str = Field(...)
    model_config = ConfigDict(extra="allow")


class Message(BaseModel):
    """One typed conversation message and its runtime metadata."""

    model_config = ConfigDict(extra="forbid")

    role: Role = Field(...)
    content: str = Field(...)
    truncation: TruncationSpec = Field(default=DEFAULT)
    message_kind: MessageKind | None = None
    endpoint: str | None = None
    model: str | None = None
    token_input: int | None = None
    token_output: int | None = None
