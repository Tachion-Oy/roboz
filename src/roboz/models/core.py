"""Core Pydantic models for messages, values, actions, and stop results."""

from __future__ import annotations

import base64
from enum import StrEnum, auto
from pathlib import Path
from typing import Annotated, Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from roboz.models._fields import RuntimeModel, RuntimeOnly
from roboz.models.truncation import DEFAULT, TruncationSpec


class AgentMode(StrEnum):
    """Supported agent execution and interaction modes."""

    DETERMINISTIC = "deterministic"
    STEERABLE = "steerable"
    AUTONOMOUS = "autonomous"

    @property
    def allows_user_interaction(self) -> bool:
        """Return whether this mode permits direct user interaction."""
        match self:
            case AgentMode.DETERMINISTIC | AgentMode.STEERABLE:
                return True
            case AgentMode.AUTONOMOUS:
                return False


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


class Image(BaseModel):
    """An HTTP(S) or base64 data URL supplied as an image input.

    Validation checks the source and encoding without fetching URLs or decoding
    image pixels. Supported formats and detail levels depend on the model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    url: str = Field(repr=False)
    detail: Literal["auto", "low", "high"] | None = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return value
        header, _, encoded = value.partition(",")
        if header in {
            "data:image/png;base64",
            "data:image/jpeg;base64",
            "data:image/webp;base64",
            "data:image/gif;base64",
        } and encoded:
            try:
                base64.b64decode(encoded, validate=True)
            except ValueError:
                raise ValueError("Image data must use valid base64 encoding") from None
            return value
        raise ValueError("Image requires an HTTP(S) URL or a supported image data URL")

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        media_type: str,
        detail: Literal["auto", "low", "high"] | None = None,
    ) -> Self:
        """Encode caller-supplied bytes as a data URL with an explicit MIME type."""
        return cls(
            url=f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}",
            detail=detail,
        )


class AttachmentMetadata(BaseModel):
    """Host-owned provenance for an attachment, independent of its image payload.

    A working path is a host-relative reference, not a promise that the file
    still exists. Hosts validate paths and control the attachment lifetime.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    working_path: str


class AgentBaseModel(RuntimeModel):
    """Common fields carried by values moving through an agent run.

    Inherited images carry runtime attachments. An input or structured-output
    model must redeclare the field to expose it as a model-facing argument.
    """

    truncation: Annotated[TruncationSpec, RuntimeOnly()] = Field(default=DEFAULT)
    images: SkipJsonSchema[list[Image]] = Field(default_factory=list, repr=False)
    attachments: Annotated[list[AttachmentMetadata], RuntimeOnly()] = Field(default_factory=list)
    message_kind: Annotated[MessageKind | None, RuntimeOnly()] = None
    endpoint: Annotated[str | None, RuntimeOnly()] = None
    model: Annotated[str | None, RuntimeOnly()] = None
    token_input: Annotated[int | None, RuntimeOnly()] = None
    token_output: Annotated[int | None, RuntimeOnly()] = None


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


class Message(RuntimeModel):
    """One typed conversation message and its runtime metadata."""

    model_config = ConfigDict(extra="forbid")

    role: Role = Field(...)
    content: str = Field(...)
    images: list[Image] = Field(default_factory=list, repr=False)
    attachments: Annotated[list[AttachmentMetadata], RuntimeOnly()] = Field(default_factory=list)
    truncation: Annotated[TruncationSpec, RuntimeOnly()] = Field(default=DEFAULT)
    message_kind: Annotated[MessageKind | None, RuntimeOnly()] = None
    endpoint: Annotated[str | None, RuntimeOnly()] = None
    model: Annotated[str | None, RuntimeOnly()] = None
    token_input: Annotated[int | None, RuntimeOnly()] = None
    token_output: Annotated[int | None, RuntimeOnly()] = None
