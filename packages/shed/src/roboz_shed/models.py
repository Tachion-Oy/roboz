from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

from roboz import FactoryCtx
from roboz.runtime import EventPipe
from roboz.models import Empty
from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny
from pydantic.json_schema import SkipJsonSchema

if TYPE_CHECKING:
    from roboz_shed.tools.cli_commands.utilities.cmd_spec import CmdSpec


class RunFileCommand(BaseModel):
    """One step in a ``run_file_command`` tool invocation."""

    command: str = Field(
        ...,
        description="Unix command: grep, cat, find, ls, tee, wc, git, etc.",
    )
    argv: list[str] = Field(
        default_factory=list,
        description="All command arguments in order (subprocess argv[1:]). Includes flags, positionals, and path-like tokens.",
    )
    stdin: str | None = Field(
        default=None,
        description="Optional stdin for commands like tee. When chain='pipe', stdin is set automatically from the previous command's output; manual stdin is for piping content into tools like tee. Framed pipe traces use placeholders for piped stdin/stdout on non-final steps to avoid echoing large intermediate output.",
    )
    model_config = ConfigDict(extra="forbid")


class RunFileCommands(Empty):
    """Input model for the ``run_file_command`` tool.

    Pass one or more commands in file_commands and always provide chain explicitly:
    pipe (stdout->stdin) or and (sequential, no passthrough).
    """

    chain: Literal["pipe", "and"] = Field(
        ...,
        description="Required chain type for execution: 'pipe' (stdout->stdin) or 'and' (sequential, no passthrough)",
    )
    file_commands: list[RunFileCommand] = Field(
        ..., min_length=1, description="List of commands to execute sequentially"
    )
    accumulated_output: SkipJsonSchema[str] = Field(
        default="",
        description="(Internal) Carries framed output across AND and pipe chain iterations.",
    )
    model_config = ConfigDict(extra="forbid")


TInput = TypeVar("TInput", bound=Empty, default=Empty)
TPayload = TypeVar("TPayload", bound=Empty, default=Empty)


class ToolValueBase(Empty):
    """Base for guarded tool values."""

    model_config = ConfigDict(extra="forbid")


class ParseError(ToolValueBase):
    """Tool input parse error that terminates a tool chain."""

    kind: Literal["parse_error"] = Field(default="parse_error")
    message: str = Field(..., description="Error message")


class Help(ToolValueBase):
    """Tool help output that terminates a tool chain."""

    kind: Literal["help"] = Field(default="help")
    message: str = Field(
        ..., description="Formatted help text listing commands and flags"
    )


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


class EmailReady(ToolValueBase):
    """Validated email attachment payload ready for guarded READ execution."""

    kind: Literal["email_ready"] = Field(default="email_ready")
    attachment_path: str = Field(
        ..., description="Absolute path of the file approved for attachment"
    )
    attachment_filename: str = Field(
        ..., description="Filename presented to the email provider"
    )


class EmailAttachmentDownloadReady(ToolValueBase):
    """Validated destination for one received-email attachment download."""

    kind: Literal["email_attachment_download_ready"] = Field(
        default="email_attachment_download_ready"
    )
    attachment_ref: str = Field(..., description="Opaque provider attachment reference")


class ActionVerdict(StrEnum):
    deny = auto()
    allow = auto()


class GuardStatus(StrEnum):
    ALLOWED = auto()
    DENIED = auto()


class GuardDenyReason(StrEnum):
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
    message: str | None = Field(default=None, description="Terminal or deny message")
    model_config = ConfigDict(extra="forbid")


@dataclass
class PermissionRule:
    """Permission rule for a specific file pattern."""

    pattern: str | Callable[..., str]
    operations: set[Operation]

    def __post_init__(self) -> None:
        if not self.operations:
            raise ValueError("operations must not be empty")


@dataclass(frozen=True)
class GuardCtx(FactoryCtx):
    """Context for write_guard used in creation of files."""

    base: Path | None
    takes_precedence: ActionVerdict
    deny: list[PermissionRule]
    allow: list[PermissionRule]
    ask: list[PermissionRule]
    default_verdict: ActionVerdict
    command_specs: Sequence[CmdSpec] = ()
    pipe: EventPipe | None = None
