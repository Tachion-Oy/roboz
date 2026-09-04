"""Factory contexts and generic resolved operations."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Literal
from pydantic import ConfigDict, Field, SerializeAsAny
from roboz import Empty, FactoryCtx
from roboz_shed.models import (
    ActionVerdict,
    GuardFileSingle,
    PermissionRule,
    TInput,
    TPayload,
)

if TYPE_CHECKING:
    from roboz_shed.tools.cli_commands.utilities.cmd_spec import CmdSpec


@dataclass(frozen=True)
class RunFileCommandsCtx(FactoryCtx):
    base: Path
    specs: Sequence["CmdSpec"]
    allow_rules: list[PermissionRule]
    deny_rules: list[PermissionRule]
    ask_rules: list[PermissionRule]
    takes_precedence: ActionVerdict
    default_verdict: ActionVerdict


@dataclass(frozen=True)
class ApplyPatchCtx(FactoryCtx):
    base: Path


@dataclass(frozen=True)
class EmailToolCtx(FactoryCtx):
    base: Path


class ResolvedFileCommand(Empty, Generic[TInput, TPayload]):
    """An operation with typed input and payloads awaiting permission checks."""

    kind: Literal["resolved_file_command"] = "resolved_file_command"
    original_input: SerializeAsAny[TInput]
    items: list[GuardFileSingle[TPayload]] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")

    @property
    def locations(self) -> list[Path]:
        return [item.location for item in self.items]
