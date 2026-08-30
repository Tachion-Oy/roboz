"""Runtime contexts and resolver output for guarded file operations."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, Field
from roboz.models import Empty
from roboz.runtime import EventPipe
from roboz.tooling import FactoryCtx

from roboz.standard.models.core import GuardFileSingle
from roboz.standard.sandbox import ActionVerdict, PermissionRule
from roboz.standard.skills.cli_commands.inputs import RunFileCommands
from roboz.standard.skills.file_edit.inputs import ApplyPatch

if TYPE_CHECKING:
    from roboz.standard.skills.cli_commands.utilities.cmd_spec import CmdSpec


@dataclass(frozen=True)
class GuardCtx(FactoryCtx):
    """Context for the shared guarded-operation stage."""

    base: Path | None
    takes_precedence: ActionVerdict
    deny: list[PermissionRule]
    allow: list[PermissionRule]
    ask: list[PermissionRule]
    default_verdict: ActionVerdict
    command_specs: Sequence["CmdSpec"] = ()
    pipe: EventPipe | None = None


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


class ResolvedFileCommand(Empty):
    """Lean guarded-operation envelope consumed by the shared guard stage."""

    kind: Literal["resolved_file_command"] = Field(default="resolved_file_command")
    original_input: RunFileCommands | ApplyPatch
    items: list[GuardFileSingle] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")

    @property
    def locations(self) -> list[Path]:
        return [item.location for item in self.items]


__all__ = [
    "ApplyPatchCtx",
    "GuardCtx",
    "ResolvedFileCommand",
    "RunFileCommandsCtx",
]
