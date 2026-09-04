"""File editing skill (apply_patch; builds on cli_tools)."""

from roboz.skill import Skill

from roboz_shed.identifiers import (
    APPLY_PATCH_TOOL_NAME,
    CLI_TOOLS_SKILL_NAME,
    FILE_EDITING_SKILL_NAME,
    RUN_FILE_COMMAND_TOOL_NAME,
)
from roboz_shed.skills.cli_tools import cli_skill
from roboz_shed.skills.file_editing.prompts import build_description, build_instructions

file_editing = Skill(
    name=FILE_EDITING_SKILL_NAME,
    description=build_description(
        cli_tools_skill_name=CLI_TOOLS_SKILL_NAME,
        run_file_command_tool_name=RUN_FILE_COMMAND_TOOL_NAME,
        apply_patch_tool_name=APPLY_PATCH_TOOL_NAME,
    ),
    instructions=build_instructions(
        cli_tools_skill_name=CLI_TOOLS_SKILL_NAME,
        run_file_command_tool_name=RUN_FILE_COMMAND_TOOL_NAME,
        apply_patch_tool_name=APPLY_PATCH_TOOL_NAME,
    ),
    depends_on=cli_skill,
)
