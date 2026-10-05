import json

import pytest

from roboz.models import Empty, LocationStr, Message, Str
from roboz.tooling import Tool
from roboz.tooling.decorators import factory, tool
from roboz.skill.core import Skill, load_skills


@tool
def sample_tool(input: Str, messages: list[Message]) -> Str:
    """A test tool"""
    return input


@tool
def sample_tool_2(input: LocationStr, messages: list[Message]) -> LocationStr:
    """A second test tool"""
    return input


def test_skill_creation():
    skill = Skill(
        name="test_skill",
        description="A test skill",
        instructions="Do something!",
        tools=[sample_tool],
    )

    assert skill.name == "test_skill"
    assert skill.description == "A test skill"
    assert "Do something!" in skill.prompt
    assert skill.tools == [sample_tool]


def test_skill_factory_builds_with_the_exact_context_on_each_call():
    calls = []

    @factory
    def read_context(input: Empty, messages: list[Message], ctx: list[str]) -> Str:
        return Str(value=", ".join(ctx))

    def build_tools(ctx: list[str]) -> list[Tool | list[Tool]]:
        calls.append(ctx)
        return [sample_tool, [read_context(ctx)]]

    build_skill = Skill.factory(
        name="context_skill",
        description="A configured skill",
        instructions="Use the configured tools.",
        build_tools=build_tools,
    )
    assert calls == []

    ctx = ["first"]
    first = build_skill(ctx)
    second = build_skill(ctx)
    assert len(calls) == 2 and all(call is ctx for call in calls)
    assert first is not second
    assert first.name == "context_skill"
    assert first.description == "A configured skill"
    assert first.instructions == "Use the configured tools."
    assert first.tools[0] is sample_tool
    assert len(first.tools) == 2
    ctx.append("second")
    assert first.tools[1](Empty(), []).value == "first, second"


def test_skill_factory_preserves_the_supplied_prerequisite():
    prerequisite = Skill(name="base", description="Base", instructions="Start here.")
    build_skill = Skill.factory(
        name="child",
        description="Child",
        instructions="Continue here.",
        build_tools=lambda ctx: [sample_tool],
        depends_on=prerequisite,
    )
    skill = build_skill(None)
    assert skill.depends_on is prerequisite
    assert "builds on the skill base" in skill.prompt
    assert skill.tools == [sample_tool]


def test_skill_load_from_json(tmp_path):
    skill_data = {
        "test_skill": {
            "description": "A test skill",
            "prompt": "Do something!",
            "tools": ["sample_tool"],
        },
        "no_tool_skill": {
            "description": "Another test",
            "prompt": "Do !",
            "tools": [],
        },
    }

    json_file = tmp_path / "skills.json"
    json_file.write_text(json.dumps(skill_data))

    skill = Skill.load_from_json(
        skill_name="no_tool_skill",
        location=json_file,
        available_tools=[],
    )
    skill = Skill.load_from_json(
        skill_name="no_tool_skill",
        location=json_file,
        available_tools=[sample_tool, sample_tool_2],
    )
    skill = Skill.load_from_json(
        skill_name="test_skill",
        location=json_file,
        available_tools=[sample_tool, sample_tool_2],
    )

    assert skill.name == "test_skill"
    assert skill.description == "A test skill"
    assert "Do something!" in skill.prompt
    assert len(skill.tools) == 1
    assert skill.tools[0].name == "sample_tool"


def test_missing_in_json_skill_load_from_json(tmp_path):
    skill_data = {
        "test_skill": {
            "description": "A test skill",
            "prompt": "Do something!",
            "tools": ["sample_tool"],
        }
    }
    json_file = tmp_path / "skills.json"
    json_file.write_text(json.dumps(skill_data))

    with pytest.raises(RuntimeError):
        _ = Skill.load_from_json(
            skill_name="test_skill",
            location=json_file,
            available_tools=[sample_tool_2],
        )


def test_missing_available_tool_skill_load_from_json(tmp_path):
    skill_data = {
        "test_skill": {
            "description": "A test skill",
            "prompt": "Do something!",
            "tools": ["sample_tool"],
        }
    }
    json_file = tmp_path / "skills.json"
    json_file.write_text(json.dumps(skill_data))

    with pytest.raises(RuntimeError):
        _ = Skill.load_from_json(
            skill_name="test_skill",
            location=json_file,
            available_tools=[sample_tool_2],
        )


def test_load_skills(tmp_path):
    skill_data = {
        "test_skill": {
            "description": "A test skill",
            "prompt": "Do something!",
            "tools": ["sample_tool"],
        },
        "test_skill_2": {
            "description": "Another test",
            "prompt": "Do!",
            "tools": ["sample_tool", "sample_tool_2"],
        },
    }

    json_file = tmp_path / "skills.json"
    json_file.write_text(json.dumps(skill_data))

    skills = load_skills(
        skill_names=["test_skill", "test_skill_2"],
        location=json_file,
        available_tools=[sample_tool, sample_tool_2],
    )
    skill_1 = skills[0]
    assert skill_1.name == "test_skill"
    assert skill_1.description == "A test skill"
    assert "Do something!" in skill_1.prompt
    assert skill_1.tools == [sample_tool]

    skill_2 = skills[1]
    assert skill_2.name == "test_skill_2"
    assert skill_2.description == "Another test"
    assert "Do!" in skill_2.prompt
    assert skill_2.tools == [sample_tool, sample_tool_2]


def test_duplicate_persisted_skill_load_skills(tmp_path):
    skill_data = {
        "test_skill": {
            "description": "A test skill",
            "prompt": "Do something!",
            "tools": ["sample_tool"],
        },
        "no_tool_skill": {
            "description": "Another test",
            "prompt": "Do !",
            "tools": ["sample_tool_2"],
        },
    }

    json_file = tmp_path / "skills.json"
    json_file.write_text(json.dumps(skill_data))
    with pytest.raises(RuntimeError):
        _ = load_skills(
            skill_names=["test_skill", "no_tool_skill"],
            location=json_file,
            available_tools=[],
        )


def test_get_skill_as_tool():
    skill = Skill(
        name="test_skill",
        description="A test skill",
        instructions="Do something!",
        tools=[sample_tool],
    )

    skill_tool = skill.get_skill_as_tool()

    assert skill_tool.name == "test_skill"
    result = skill_tool(Empty(), [])
    assert "Do something!" in result.value
    assert "## Available Tools" in result.value
    assert "sample_tool" in result.value
    assert "input Pydantic model" in result.value
    assert "action" in result.value
    assert "rationale" in result.value


def test_get_skill_as_tool_without_tools():
    skill = Skill(
        name="no_tool_skill",
        description="A skill without bundled tools",
        instructions="Only instructions.",
        tools=[],
    )

    result = skill.get_skill_as_tool()(Empty(), [])
    assert "Only instructions." in result.value
    assert "## Available Tools" not in result.value


def test_get_skill_as_tool_closure():
    skill_1 = Skill(
        name="test_skill_1",
        description="A test skill 1",
        instructions="Do something 1!",
        tools=[sample_tool],
    )
    skill_2 = Skill(
        name="test_skill_2",
        description="A test skill 2",
        instructions="Do something 2!",
        tools=[sample_tool],
    )

    skill_tool_1 = skill_1.get_skill_as_tool()
    skill_tool_2 = skill_2.get_skill_as_tool()

    assert skill_tool_1.name == "test_skill_1"
    assert "Do something 1!" in skill_tool_1(Empty(), []).value

    assert skill_tool_2.name == "test_skill_2"
    assert "Do something 2!" in skill_tool_2(Empty(), []).value
