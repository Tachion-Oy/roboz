"""Generic resolved operations passed through tool guard chains."""

from pathlib import Path
from typing import TYPE_CHECKING, Generic, Literal

from pydantic import ConfigDict, Field, SerializeAsAny

from roboshed.models import (
    GuardFileSingle,
    TInput,
    TPayload,
)
from roboz.models import Empty

if TYPE_CHECKING:
    pass


class ResolvedFileCommand(Empty, Generic[TInput, TPayload]):
    """An operation with typed input and payloads awaiting permission checks."""

    kind: Literal["resolved_file_command"] = "resolved_file_command"
    original_input: SerializeAsAny[TInput]
    items: list[GuardFileSingle[TPayload]] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")

    @property
    def locations(self) -> list[Path]:
        """Return all filesystem locations requiring permission checks."""
        return [item.location for item in self.items]
