"""Generic resolved operations passed through tool guard chains."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Generic, Literal

from pydantic import ConfigDict, Field, SerializeAsAny

from roboz.shed.models import (
    GuardFileSingle,
    TInput,
    TPayload,
)
from roboz.models import Empty
from roboz.models._fields import RuntimeOnly
from roboz.models.truncation import NO_MESSAGE, TruncationSpec

if TYPE_CHECKING:
    pass


class ResolvedFileCommand(Empty, Generic[TInput, TPayload]):
    """An operation with typed input and payloads awaiting permission checks.

    It is input for the guard, which always follows it, and never part of the
    model's context: a recursive command lists every file it may touch.
    """

    kind: Literal["resolved_file_command"] = "resolved_file_command"
    truncation: Annotated[TruncationSpec, RuntimeOnly()] = Field(default=NO_MESSAGE)
    original_input: SerializeAsAny[TInput]
    items: list[GuardFileSingle[TPayload]] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")

    @property
    def locations(self) -> list[Path]:
        """Return all filesystem locations requiring permission checks."""
        return [item.location for item in self.items]
