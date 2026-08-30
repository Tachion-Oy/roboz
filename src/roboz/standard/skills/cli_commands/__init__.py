"""Constrained CLI commands skill."""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboz.runtime import EventPipe
from roboz.skill import Skill

from roboz.standard.identifiers import CLI_COMMANDS_SKILL_NAME
from roboz.standard.skills.cli_commands.prompts import DESCRIPTION, INSTRUCTIONS
if TYPE_CHECKING:
    from roboz.standard.sandbox import Project


def build_skill(project: Project, pipe: EventPipe) -> Skill:
    """Build the CLI commands skill for one project runtime."""
    from roboz.standard.skills.cli_commands.tools import (
        FILE_COMMANDS_DELETE,
        FILE_COMMANDS_READ,
        FILE_COMMANDS_WRITE,
        get_run_file_command,
    )

    return Skill(
        name=CLI_COMMANDS_SKILL_NAME,
        description=DESCRIPTION,
        instructions=INSTRUCTIONS,
        tools=get_run_file_command(
            **project.workspace_permissions(),
            pipe=pipe,
            command_specs=(
                FILE_COMMANDS_READ + FILE_COMMANDS_WRITE + FILE_COMMANDS_DELETE
            ),
            cli_skill_name=CLI_COMMANDS_SKILL_NAME,
        ),
    )


__all__ = ["build_skill"]
