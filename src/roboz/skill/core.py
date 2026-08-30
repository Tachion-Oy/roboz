from __future__ import annotations

import json
from collections.abc import Sequence
from logging import getLogger
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from roboz._naming import validate_public_name
from roboz.models import Empty, Message, Str
from roboz.tooling._prompts import get_tool_instructions
from roboz.tooling.core import Tool
from roboz.tooling.decorators import tool

logger = getLogger(__name__)

# Header that every skill prompt starts with; keep this as the single source of
# truth for prompt presentation.
SKILL_INSTRUCTIONS_PREFIX: Final[str] = "# Instructions"


class SkillModel(BaseModel):
    name: str
    description: str
    prompt: str
    tools: list[str]


class Skill:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        tools: Sequence[Tool | Sequence[Tool]] | None = None,
        depends_on: Skill | None = None,
    ):
        self.name = name
        self.description = description
        self.instructions = instructions
        self.tools = Tool.to_tool_list(tools)
        self.prompt = f"{SKILL_INSTRUCTIONS_PREFIX}\n\n{instructions}"
        self.depends_on = depends_on
        if depends_on is not None:
            self.prompt = (
                f"# Important Note\n\n this skill builds on the skill {depends_on.name} "
                "and makes use of tools and instructions defined there.\n\n"
            ) + self.prompt

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = validate_public_name(value, kind="skill")

    def copy(
        self,
        *,
        tools: Sequence[Tool | Sequence[Tool]] | None = None,
    ) -> Skill:
        """Create a separate skill, optionally replacing its tools."""
        return Skill(
            name=self.name,
            description=self.description,
            instructions=self.instructions,
            tools=self.tools if tools is None else tools,
            depends_on=self.depends_on,
        )

    @classmethod
    def load_from_json(
        cls, *, skill_name: str, location: Path, available_tools: Sequence[Tool]
    ) -> Skill:
        raw_skills = json.loads(location.read_text())
        try:
            raw_skill: dict = raw_skills[skill_name]
            raw_skill["name"] = skill_name
        except KeyError:
            logger.error(f"the skill {skill_name=} is not available in {location=}")
            raise RuntimeError
        skill = SkillModel(**raw_skill)
        if set(skill.tools) - {t.name for t in available_tools}:
            logger.error(
                f"{skill_name=} invokes {skill.tools=} not all in {available_tools=}"
            )
            raise RuntimeError
        tools = [tool for tool in available_tools if tool.name in skill.tools]
        return cls(
            name=skill.name,
            description=skill.description,
            instructions=skill.prompt,
            tools=tools,
        )

    def get_skill_as_tool(self) -> Tool[Empty, Str]:
        @tool
        def show_skill_instructions(input: Empty, messages: list[Message]) -> Str:
            return Str(value=self._loaded_message())

        show_skill_instructions.name = self.name
        show_skill_instructions.description = f"The SKILL '{self.name}' can be loaded with this tool.\nDescription: {self.description}"
        return show_skill_instructions

    def _loaded_message(self) -> str:
        if not self.tools:
            return self.prompt
        tool_instructions = get_tool_instructions(
            self.tools, with_agent_tool_use_instructions=False
        )
        return f"{self.prompt}\n\n{tool_instructions}"


def load_skills(
    *, skill_names: list[str], location: Path, available_tools: Sequence[Tool]
) -> list[Skill]:
    return [
        Skill.load_from_json(
            skill_name=skill_name,
            location=location,
            available_tools=available_tools,
        )
        for skill_name in skill_names
    ]
