"""Shared models for the lean package's guarded tool flows."""

from __future__ import annotations

from enum import StrEnum, auto
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from roboz.models import Empty
from roboz.standard.sandbox import ActionVerdict, Operation
from roboz.standard.skills.cli_commands.inputs import RunFileCommands
from roboz.standard.skills.file_edit.inputs import ApplyPatch


class GuardStatus(StrEnum):
    ALLOWED = auto()
    DENIED = auto()


class GuardDenyReason(StrEnum):
    USER_DECLINED = auto()
    POLICY_DENIED = auto()


class ToolValueBase(Empty):
    """Base class for guarded payloads shipped in the lean package."""

    model_config = ConfigDict(extra="forbid")


class ParseError(ToolValueBase):
    kind: Literal["parse_error"] = Field(default="parse_error")
    message: str = Field(..., description="Error message")


class Help(ToolValueBase):
    kind: Literal["help"] = Field(default="help")
    message: str = Field(..., description="Formatted help text")


class CommandReady(ToolValueBase):
    kind: Literal["command_ready"] = Field(default="command_ready")
    command_name: str
    argv: list[str]
    base_workdir: Path
    display_command: str
    stdin: str | None = None


class ApplyPatchReady(ToolValueBase):
    kind: Literal["apply_patch_ready"] = Field(default="apply_patch_ready")
    path: str
    old_string: str
    new_string: str
    replace_all: bool


class GuardFileSingle(BaseModel):
    """One location and guarded payload awaiting a guard verdict."""

    operation: Operation
    location: Path
    value: CommandReady | ApplyPatchReady = Field(discriminator="kind")
    model_config = ConfigDict(extra="forbid")


class GuardFileSingleResult(BaseModel):
    location: Path
    value: CommandReady | ApplyPatchReady = Field(discriminator="kind")
    verdict: ActionVerdict
    model_config = ConfigDict(extra="forbid")


class GuardFilesResult(Empty):
    status: GuardStatus = GuardStatus.ALLOWED
    deny_reason: GuardDenyReason | None = None
    items: list[GuardFileSingleResult]
    original_input: RunFileCommands | ApplyPatch
    message: str | None = None
    model_config = ConfigDict(extra="forbid")


__all__ = [
    "ApplyPatchReady",
    "CommandReady",
    "GuardDenyReason",
    "GuardFileSingle",
    "GuardFileSingleResult",
    "GuardFilesResult",
    "GuardStatus",
    "Help",
    "ParseError",
    "ToolValueBase",
]
