import json
from pathlib import Path

import pytest

from roboz import Empty, Message, Str, factory, tool
from roboz.skill import Skill


@tool
def valid_tool(input: Empty, messages: list[Message]) -> Str:
    return Str(value="ok")


@factory
def valid_factory(input: Empty, messages: list[Message], ctx: None) -> Str:
    return Str(value="ok")


@pytest.mark.parametrize(
    "name",
    ["", "UpperCase", "kebab-case", "two words", "_private", "trailing_", "café"],
)
def test_tool_rejects_invalid_names_on_every_assignment_path(name: str) -> None:
    with pytest.raises(ValueError, match="lowercase ASCII snake_case"):
        valid_tool.rename(name)
    with pytest.raises(ValueError, match="lowercase ASCII snake_case"):
        valid_tool.copy(name=name)
    with pytest.raises(ValueError, match="lowercase ASCII snake_case"):
        valid_tool.name = name
    assert valid_tool.name == "valid_tool"


def test_factory_rejects_invalid_direct_assignment() -> None:
    with pytest.raises(ValueError, match="lowercase ASCII snake_case"):
        valid_factory.name = "invalid-factory"
    assert valid_factory.name == "valid_factory"


def test_decorators_reject_invalid_function_names() -> None:
    with pytest.raises(ValueError, match="lowercase ASCII snake_case"):

        @tool
        def InvalidTool(input: Empty, messages: list[Message]) -> Str:
            return Str(value="invalid")

    with pytest.raises(ValueError, match="lowercase ASCII snake_case"):

        @factory
        def InvalidFactory(input: Empty, messages: list[Message], ctx: None) -> Str:
            return Str(value="invalid")


@pytest.mark.parametrize("name", ["skill_2", "cli_commands", "pdf"])
def test_skill_accepts_valid_names(name: str) -> None:
    skill = Skill(name=name, description="test", instructions="test")
    assert skill.name == name
    assert skill.get_skill_as_tool().name == name


@pytest.mark.parametrize("name", ["", "BadSkill", "bad-skill", "bad skill", "_bad"])
def test_skill_rejects_invalid_names_on_construction_and_assignment(name: str) -> None:
    with pytest.raises(ValueError, match="lowercase ASCII snake_case"):
        Skill(name=name, description="test", instructions="test")

    skill = Skill(name="valid_skill", description="test", instructions="test")
    with pytest.raises(ValueError, match="lowercase ASCII snake_case"):
        skill.name = name
    assert skill.name == "valid_skill"


def test_names_must_be_strings() -> None:
    with pytest.raises(TypeError, match="tool name must be a string"):
        valid_tool.name = 1  # type: ignore[assignment]
    with pytest.raises(TypeError, match="skill name must be a string"):
        Skill(name=1, description="test", instructions="test")  # type: ignore[arg-type]


def test_json_skill_loading_validates_the_selected_name(tmp_path: Path) -> None:
    location = tmp_path / "skills.json"
    location.write_text(
        json.dumps(
            {
                "invalid-skill": {
                    "description": "test",
                    "prompt": "test",
                    "tools": [],
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="lowercase ASCII snake_case"):
        Skill.load_from_json(
            skill_name="invalid-skill", location=location, available_tools=[]
        )
