from __future__ import annotations

from enum import StrEnum, auto
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from roboz.models.truncation import DEFAULT, TruncationSpec


class BaseNames(StrEnum):
    ACTION_FIELD = "action"
    RATIONALE_FIELD = "rationale"
    CALLER_FIELD = "caller"
    VALUE_FIELD = "value"
    KIND_FIELD = "kind"


class Role(StrEnum):
    SYSTEM = auto()
    ASSISTANT = auto()
    USER = auto()
    ERROR = auto()


class MessageKind(StrEnum):
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
    truncation: TruncationSpec = Field(default=DEFAULT)
    message_kind: MessageKind | None = None
    endpoint: str | None = None
    model: str | None = None
    token_input: int | None = None
    token_output: int | None = None


class Empty(AgentBaseModel): ...


class Stop(AgentBaseModel):
    value: str | None = Field(default=None)
    model_config = ConfigDict(extra="forbid")


class All(Empty):
    model_config = ConfigDict(extra="allow")


class Int(Empty):
    value: int = Field(...)
    model_config = ConfigDict(extra="forbid")


class Str(Empty):
    value: str = Field(...)
    model_config = ConfigDict(extra="forbid")


class Location(Empty):
    value: Path = Field(...)
    model_config = ConfigDict(extra="forbid")


class Strs(Empty):
    items: list[str] = Field(...)
    model_config = ConfigDict(extra="forbid")


class LocationStr(Empty):
    location: Path = Field(...)
    value: str = Field(...)
    model_config = ConfigDict(extra="forbid")


class StopLocation(Stop):
    location: Path = Field(...)
    model_config = ConfigDict(extra="forbid")


class HashMaps(Empty):
    items: list[dict] = Field(...)
    model_config = ConfigDict(extra="forbid")


class Invoke(AgentBaseModel):
    action: str = Field(...)
    rationale: str = Field(...)
    model_config = ConfigDict(extra="allow")


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role = Field(...)
    content: str = Field(...)
    truncation: TruncationSpec = Field(default=DEFAULT)
    message_kind: MessageKind | None = None
    endpoint: str | None = None
    model: str | None = None
    token_input: int | None = None
    token_output: int | None = None
