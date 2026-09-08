"""Draft-only email tool guidance skill."""

from roboz.skill import Skill

from roboshed.identifiers import EMAIL_TOOLS_SKILL_NAME
from roboshed.skills.email_tools.prompts import DESCRIPTION, INSTRUCTIONS

email_skill = Skill(
    name=EMAIL_TOOLS_SKILL_NAME, description=DESCRIPTION, instructions=INSTRUCTIONS
)
