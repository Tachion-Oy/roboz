"""Filesystem instructions and guarded tools bound to one runtime policy."""

from dataclasses import dataclass

from roboz.runtime import EventPipe
from roboz.skill import Skill
from roboz.tooling import Tool

from roboz.shed.identifiers import FILESYSTEM_SKILL_NAME
from roboz.shed.sandbox import PermissionPolicy
from roboz.shed.tools.apply_patch import get_apply_patch
from roboz.shed.tools.cli_commands import get_run_file_command

from .prompts import DESCRIPTION, INSTRUCTIONS


@dataclass(frozen=True)
class FilesystemContext:
    """Explicit file permissions and the owning agent's runtime event pipe."""

    permissions: PermissionPolicy
    pipe: EventPipe


def _build_tools(ctx: FilesystemContext) -> list[Tool]:
    options = ctx.permissions.tool_options(ctx.pipe)
    return [*get_run_file_command(**options), *get_apply_patch(**options)]


filesystem_skill = Skill.factory(
    name=FILESYSTEM_SKILL_NAME,
    description=DESCRIPTION,
    instructions=INSTRUCTIONS,
    build_tools=_build_tools,
)

__all__ = ["FilesystemContext", "filesystem_skill"]
