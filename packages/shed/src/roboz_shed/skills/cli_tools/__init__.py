"""Constrained CLI tools skill definition."""

from roboz.skill import Skill

from roboz_shed.identifiers import CLI_TOOLS_SKILL_NAME
from roboz_shed.skills.cli_tools.prompts import DESCRIPTION, INSTRUCTIONS

cli_skill = Skill(
    name=CLI_TOOLS_SKILL_NAME, description=DESCRIPTION, instructions=INSTRUCTIONS
)
