from __future__ import annotations

import logging
from typing import Final, Sequence

from roboz.skill.core import Skill

logger = logging.getLogger(__name__)

GENERAL_SKILL_INSTRUCTIONS: Final[str] = """## Skill definition

A skill is a specialized set of instructions *and* tools providing expert knowledge and detailed instructions for specific broader tasks. You may think of an individual skill as a stored set of of instructions and tools that can be loaded when needed, but is not automatically available. All existing skills will be given to you as a list with a name and a short description."""

SKILL_INSTANTIATION_INSTRUCTIONS: Final[str] = """## Skill instantiation

Skills are instantiated/invoked by calling them by their name with the same syntax as for a tool. A skill invocation will then provide you via a message the detailed instructions and references on how the specific skill is used and the tools that go with it. Skill loading is per conversation/run: if a memory or prior transcript says a skill was loaded earlier, that does not make that skill (or its tools) available in the current conversation. You must invoke the skill again in the current run before using tools defined by that skill, because those tools are injected only after the skill is loaded. When a user request or the task you are executing matches a skill's domain, you should first invoke the relevant skill to access detailed implementation guidance, best practices, and proven workflows. However a specific skill may have more detailed invocation instructions, such as 'only use when specifically requested by the user', which you must respect at all times."""

AGENT_SKILL_USE_INSTRUCTIONS: Final[str] = f"""# Instructions on Skill Usage

{GENERAL_SKILL_INSTRUCTIONS}

{SKILL_INSTANTIATION_INSTRUCTIONS}"""

EXAMPLE_SKILL_INSTRUCTIONS = (
    "You are now using the Example Skill. This skill provides specialized "
    "capabilities. You now have access to all tools and information presented here."
)


def _get_single_skill_instruction(skill: Skill) -> str:
    skill_use_prompt = f"### `{skill.name}`\n"
    skill_use_prompt += f"description: {skill.description}"
    return skill_use_prompt


def get_skill_instructions(
    skills: Sequence[Skill | None] | Sequence[Skill],
) -> str:
    if not skills:
        return ""
    skill_use_prompt: str = (
        f"""{AGENT_SKILL_USE_INSTRUCTIONS}\n\n## Available Skills\n\n"""
    )
    for s in skills:
        if s is None:
            continue
        skill_use_prompt += f"{_get_single_skill_instruction(s)}\n\n"
    return skill_use_prompt.rstrip()
