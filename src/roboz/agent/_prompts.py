"""System-prompt assembly for model-driven agent runs."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Sequence

from roboz.skill._prompts import AGENT_SKILL_USE_INSTRUCTIONS
from roboz.tooling._prompts import AGENT_TOOL_USE_INSTRUCTIONS, _get_single_instruction

if TYPE_CHECKING:
    from roboz.skill.core import Skill
    from roboz.tooling.core import Tool

logger = logging.getLogger(__name__)


def _get_available_tools_and_skills_section(
    tools: Sequence[Tool] | None,
    skills: Sequence[Skill] | None,
) -> str:
    tools = tools if tools is not None else []
    skills = skills if skills is not None else []
    skill_names = {s.name for s in skills if s is not None}
    section = "## Available tools and skills\n\n"
    for t in tools:
        if t is None or t.chained_to or t.name in skill_names:
            continue
        section += f"{_get_single_instruction(t)}\n\n"
    for s in skills:
        if s is None:
            continue
        section += f"{_get_single_instruction(s.get_skill_as_tool())}\n\n"
    return section.rstrip()


def get_example_agent_discussion(*, allow_user_input: bool = True) -> str:
    """Return a multi-turn example showing how turns appear in the agent (roles and JSON shapes)."""
    section = "## Example discussion\n\n<example>\n\n"
    section += (
        "The following is a **full multi-turn discussion**: many separate "
        "Assistant/User rounds over time—not one assistant message. Each "
        "`Assistant:` line is exactly **one** assistant reply containing **one** "
        "JSON object; never paste several JSON objects into a single assistant "
        "reply.\n\n"
    )
    section += (
        "System: This is an example system message for illustrative purposes.\n\n"
    )
    if not allow_user_input:
        section += "Assistant: "
        section += (
            json.dumps(
                {
                    "action": "example_tool",
                    "rationale": "Calling the example tool.",
                    "value": 42,
                }
            )
            + "\n\n"
        )
        section += "User: "
        section += json.dumps(
            {"caller": "example_tool", "value": "Example: 42"}
        )
        section += "\n\n</example>"
        return section
    section += "Assistant: "
    section += (
        json.dumps(
            {
                "action": "prompt_user",
                "rationale": "Getting the user's request.",
                "value": "Hi there! How should we start the example?",
            }
        )
        + "\n\n"
    )
    section += "User: "
    section += (
        json.dumps(
            {
                "caller": "prompt_user",
                "value": "Please demonstrate the example tool by calling it with value 42.",
            }
        )
        + "\n\n"
    )
    section += "Assistant: "
    section += (
        json.dumps(
            {
                "action": "example_tool",
                "rationale": "Calling the example_tool to demonstrate its structure as requested.",
                "value": 42,
            }
        )
        + "\n\n"
    )
    section += "User: "
    section += (
        json.dumps(
            {
                "caller": "example_tool",
                "value": "Example: 42",
            }
        )
        + "\n\n"
    )
    section += "Assistant: "
    section += (
        json.dumps(
            {
                "action": "prompt_user",
                "rationale": "Getting the user's next request.",
                "value": "Anything else I can help you with?",
            }
        )
        + "\n\n"
    )
    section += "User: "
    section += (
        json.dumps(
            {
                "caller": "prompt_user",
                "value": "Please load the example skill.",
            }
        )
        + "\n\n"
    )
    section += "Assistant: "
    section += (
        json.dumps(
            {
                "action": "example_skill",
                "rationale": "Loading the example_skill as requested.",
            }
        )
        + "\n\n"
    )
    section += "User: "
    section += json.dumps(
        {
            "caller": "example_skill",
            "value": "# Instructions\n\nYou are now using the Example Skill... "
            "<redacted the rest as this is an example>",
        }
    )
    section += "\n\n</example>"
    return section


def get_agentic_system_prompt(
    *,
    system_prompt: str = "",
    tools: Sequence[Tool] | None = None,
    skills: Sequence[Skill] | None = None,
    allow_user_input: bool = True,
) -> str:
    """Build the complete technical prompt for an agentic run.

    Keep detailed tool and skill calling instructions out of the supplied
    system prompt because this builder generates them consistently.
    """
    parts = [
        system_prompt.strip(),
        AGENT_TOOL_USE_INSTRUCTIONS,
        AGENT_SKILL_USE_INSTRUCTIONS,
        _get_available_tools_and_skills_section(tools, skills),
        get_example_agent_discussion(allow_user_input=allow_user_input),
    ]
    return "\n\n".join(p for p in parts if p)
