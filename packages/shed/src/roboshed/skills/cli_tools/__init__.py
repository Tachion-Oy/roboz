"""Constrained CLI tools skill definition."""

from roboz.skill import Skill

from roboshed.identifiers import CLI_TOOLS_SKILL_NAME
from roboshed.skills.cli_tools.prompts import DESCRIPTION, INSTRUCTIONS

cli_skill = Skill(
    name=CLI_TOOLS_SKILL_NAME, description=DESCRIPTION, instructions=INSTRUCTIONS
)
