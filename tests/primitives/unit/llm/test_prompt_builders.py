"""Unit tests for prompt building."""

import json

from roboz.agent._prompts import get_agentic_system_prompt
from roboz.llm import get_basic_system_prompt
from roboz.models import BaseNames, Empty, Message, Str
from roboz.models._schema import extract_fields_and_type_names
from roboz.tooling._prompts import (
    example_skill_tool,
    example_tool,
)
from roboz.tooling.decorators import tool


@tool
def empty_input_tool(input: Empty, messages: list[Message]) -> Str:
    """Tool with Empty input, no required fields beyond action and rationale."""
    return Str(value="ok")


def test_get_agentic_system_prompt_with_required_input():
    """Tool with required input (Int) produces valid prompt."""
    tools = [example_tool]
    prompt = get_agentic_system_prompt(system_prompt="Test.", tools=tools, skills=None)
    assert "example_tool" in prompt
    assert "## Available tools and skills" in prompt

    excluded_fields, _ = extract_fields_and_type_names()
    expected_fields = (
        {
            BaseNames.ACTION_FIELD.value,
            BaseNames.RATIONALE_FIELD.value,
        }
        | set(example_tool.InputModel.model_fields)
    ) - excluded_fields

    for field_name in expected_fields:
        assert field_name in prompt


def test_get_agentic_system_prompt_with_empty_input():
    """Tool with Empty input produces valid prompt."""
    tools = [empty_input_tool]
    prompt = get_agentic_system_prompt(system_prompt="Test.", tools=tools, skills=None)
    assert "empty_input_tool" in prompt


def test_get_agentic_system_prompt_contains_example_discussion():
    """Agentic prompt ends with Example discussion section."""
    prompt = get_agentic_system_prompt(
        system_prompt="Test.", tools=[example_tool], skills=None
    )
    assert "## Example discussion" in prompt
    assert "multi-turn discussion" in prompt
    assert "System:" in prompt
    assert "Assistant:" in prompt
    assert "User:" in prompt
    assert "prompt_user" in prompt
    assert '"action"' in prompt
    assert '"caller"' in prompt


def test_get_agentic_system_prompt_includes_json_shape_and_self_check():
    """Tool instructions include illustrative valid/invalid JSON and pre-send check."""
    prompt = get_agentic_system_prompt(
        system_prompt="Test.", tools=[example_tool], skills=None
    )
    assert "## JSON shape (illustrative)" in prompt
    assert "## Before you send" in prompt
    assert "}{" in prompt
    assert "never do this" in prompt.lower()


def test_get_basic_system_prompt_uses_valid_schema_and_matching_example():
    prompt = get_basic_system_prompt(
        system_prompt="Return a summary.",
        OutputModel=Str,
        output_example={"value": "## Summary\n- Durable fact."},
    )

    json_blocks = [
        block.split("\n```", 1)[0] for block in prompt.split("```json\n")[1:]
    ]
    schema, example = map(json.loads, json_blocks)

    assert schema["properties"] == {"value": {"title": "Value", "type": "string"}}
    assert schema["required"] == ["value"]
    assert example == {"value": "## Summary\n- Durable fact."}
    assert "'properties'" not in prompt
    assert '"action"' not in prompt
    assert "selected tool" not in prompt


def test_get_agentic_system_prompt_tools_before_skills():
    """Plain tools appear before skills in Available tools and skills section."""
    from roboz.skill.core import Skill

    skill = Skill(
        name="test_skill",
        description="A skill.",
        instructions="Do things.",
        tools=[example_skill_tool],
    )
    prompt = get_agentic_system_prompt(
        system_prompt="You are helpful.",
        tools=[example_tool],
        skills=[skill],
    )
    tools_section_start = prompt.index("## Available tools and skills")
    tools_section = prompt[tools_section_start:]
    example_tool_pos = tools_section.find("example_tool")
    test_skill_pos = tools_section.find("test_skill")
    assert example_tool_pos < test_skill_pos


def test_get_agentic_system_prompt_with_skills():
    """Full agentic prompt with tools and skills produces valid prompt."""
    from roboz.skill.core import Skill

    skill = Skill(
        name="test_skill",
        description="A skill.",
        instructions="Do things.",
        tools=[example_skill_tool],
    )
    prompt = get_agentic_system_prompt(
        system_prompt="You are helpful.",
        tools=[example_tool],
        skills=[skill],
    )
    assert "example_tool" in prompt
    assert "test_skill" in prompt
    fields, _ = extract_fields_and_type_names()
    # "model" appears in prose (e.g. "pydantic model"); scrub check is substring-based.
    skip_substring_scrub = frozenset({"model"})
    for f in fields:
        if f in skip_substring_scrub:
            continue
        assert f not in prompt.lower()


def test_get_agentic_system_prompt_mentions_skills_are_per_run():
    """Skill usage instructions emphasize re-invocation in each run."""
    prompt = get_agentic_system_prompt(
        system_prompt="Test.",
        tools=[example_tool],
        skills=None,
    )
    assert "Skill loading is per conversation/run" in prompt
    assert (
        "that does not make that skill (or its tools) available in the current conversation"
        in prompt
    )
    assert "You must invoke the skill again in the current run" in prompt


def test_skill_prompt_instructions_only():
    """Skill.prompt contains instructions only, no embedded tool listing."""
    from roboz.skill.core import Skill

    skill = Skill(
        name="test_skill",
        description="A skill.",
        instructions="Do things.",
        tools=[example_skill_tool],
    )
    assert "# Instructions" in skill.prompt
    assert "Do things." in skill.prompt
    assert "## Available Tools" not in skill.prompt
