"""File-edit skill and its guarded apply-patch tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboz.runtime import EventPipe
from roboz.skill import Skill

from roboz.standard.identifiers import (
    APPLY_PATCH_TOOL_NAME,
    CLI_COMMANDS_SKILL_NAME,
    FILE_EDIT_SKILL_NAME,
    RUN_FILE_COMMAND_TOOL_NAME,
)
from roboz.standard.skills.file_edit.prompts import build_description, build_instructions
if TYPE_CHECKING:
    from roboz.standard.sandbox import Project


def build_skill(project: Project, pipe: EventPipe) -> Skill:
    """Build the file-edit skill for one project runtime."""
    from roboz.standard.skills.file_edit.tools import get_apply_patch

    return Skill(
        name=FILE_EDIT_SKILL_NAME,
        description=build_description(
            cli_commands_skill_name=CLI_COMMANDS_SKILL_NAME,
            run_file_command_tool_name=RUN_FILE_COMMAND_TOOL_NAME,
            apply_patch_tool_name=APPLY_PATCH_TOOL_NAME,
        ),
        instructions=build_instructions(
            cli_commands_skill_name=CLI_COMMANDS_SKILL_NAME,
            run_file_command_tool_name=RUN_FILE_COMMAND_TOOL_NAME,
            apply_patch_tool_name=APPLY_PATCH_TOOL_NAME,
        ),
        tools=get_apply_patch(
            **project.workspace_permissions(),
            pipe=pipe,
            file_edit_skill_name=FILE_EDIT_SKILL_NAME,
        ),
    )


__all__ = ["build_skill"]
