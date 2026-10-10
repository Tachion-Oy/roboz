"""Shared command, guard, and chaining models for Shed tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from roboz.models import Empty

if TYPE_CHECKING:
    pass


TInput = TypeVar("TInput", bound=Empty, default=Empty)
TPayload = TypeVar("TPayload", bound=Empty, default=Empty)


class ToolValueBase(Empty):
    """Base for guarded tool values."""

    model_config = ConfigDict(extra="forbid")


class ParseError(ToolValueBase):
    """Tool input parse error that terminates a tool chain."""

    kind: Literal["parse_error"] = Field(default="parse_error")
    message: str = Field(..., description="Error message")


class CommandReady(ToolValueBase):
    """Guarded command ready for subprocess execution."""

    kind: Literal["command_ready"] = Field(default="command_ready")
    command_name: str = Field(..., description="Validated command specification name")
    argv: list[str] = Field(..., description="Full subprocess argv to run")
    base_workdir: Path = Field(..., description="Resolved base working directory")
    display_command: str = Field(..., description="Human-readable command label")
    stdin: str | None = Field(default=None, description="Optional subprocess stdin")


class ApplyPatchReady(ToolValueBase):
    """Validated apply_patch payload ready for guarded execution."""

    kind: Literal["apply_patch_ready"] = Field(default="apply_patch_ready")
    path: str = Field(..., description="Relative path to the guarded file")
    old_string: str = Field(
        ...,
        description=(
            "Literal substring to match. Use empty string to rewrite the full file with new_string."
        ),
    )
    new_string: str = Field(..., description="Replacement text")
    replace_all: bool = Field(
        ...,
        description="Whether to replace all occurrences or require exactly one match",
    )


class ActionVerdict(StrEnum):
    """Permission decision applied by a matching guard rule."""

    deny = auto()
    allow = auto()


class GuardStatus(StrEnum):
    """Aggregate outcome of guarding a resolved operation."""

    ALLOWED = auto()
    DENIED = auto()


class GuardDenyReason(StrEnum):
    """Machine-readable reason a guarded operation was denied."""

    USER_DECLINED = auto()
    POLICY_DENIED = auto()


class Operation(StrEnum):
    """Type of operation (also used as permission)."""

    READ = auto()
    CREATE = auto()
    DELETE = auto()
    STAGE = auto()


class GuardFileSingle(BaseModel, Generic[TPayload]):
    """Single file/location to guard. The payload belongs to the calling tool."""

    operation: Operation = Field(description="The operation type")
    location: Path = Field(description="The access location")
    value: SerializeAsAny[TPayload]
    model_config = ConfigDict(extra="forbid")


class GuardFileSingleResult(BaseModel, Generic[TPayload]):
    """Result for a single guarded location. The payload retains its original type."""

    location: Path = Field(description="The access location")
    value: SerializeAsAny[TPayload]
    verdict: ActionVerdict = Field(description="Allow or deny operation")
    model_config = ConfigDict(extra="forbid")


class ApplyPatch(Empty):
    """Single-file apply_patch input (literal find/replace)."""

    path: str = Field(
        ...,
        description=(
            "Single file path to edit (relative to the tool base or absolute). "
            "Permission rules may still be configured as relative or absolute patterns."
        ),
    )
    old_string: str = Field(
        ...,
        description=(
            "Exact substring to find in the file. "
            "Use empty string to rewrite the full file with new_string."
        ),
    )
    new_string: str = Field(
        ...,
        description="Replacement text (may be empty to delete the matched substring)",
    )
    replace_all: bool = Field(
        default=False,
        description=(
            "If false: old_string must occur exactly once. "
            "If true: replace every occurrence (at least one required)."
        ),
    )
    model_config = ConfigDict(extra="forbid")


class GuardFilesResult(Empty, Generic[TInput, TPayload]):
    """Guard outcome retaining typed payloads and the originating tool input."""

    status: GuardStatus = Field(
        default=GuardStatus.ALLOWED, description="Guard outcome"
    )
    deny_reason: GuardDenyReason | None = Field(
        default=None,
        description="Machine-readable deny reason when status is DENIED",
    )
    items: list[GuardFileSingleResult[TPayload]] = Field(
        description="Results for individual sentries"
    )
    original_input: SerializeAsAny[TInput]
    message: str | None = Field(
        default=None, description="Approval questions and replies, or a denial diagnostic"
    )
    model_config = ConfigDict(extra="forbid")


@dataclass
class PermissionRule:
    """Permission rule for a specific file pattern."""

    pattern: str | Callable[..., str]
    operations: set[Operation]

    def __post_init__(self) -> None:
        """Require at least one operation on every permission rule."""
        if not self.operations:
            raise ValueError("operations must not be empty")
