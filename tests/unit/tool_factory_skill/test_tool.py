import pytest
from pydantic import BaseModel

from roboz import Ctx
from roboz.models import Empty, Int, Invoke, Message, Str
from roboz.tooling.core import Tool
from roboz.tooling.decorators import factory, tool


class Invalid(BaseModel): ...


def test_tool_copy_id_changes():
    @tool
    def my_tool(input: Str, messages: list[Message]) -> Str:
        return input

    copied_tool = my_tool.copy()
    assert my_tool.id != copied_tool.id


def test_factory_tool_id_stable():

    @factory
    def my_factory(input: Str, messages: list[Message], ctx: Ctx) -> Str:
        return input

    f_tool1 = my_factory(Ctx())
    f_tool2 = my_factory(Ctx())

    assert f_tool1.id == my_factory.id
    assert f_tool2.id == my_factory.id
    assert f_tool1.id == f_tool2.id


def test_create_tool():
    @tool
    def static_tool(input: Str, messages: list[Message]) -> Str:
        """This is a static Tool"""
        return Str(value=input.value * 2)

    @tool
    def dynamic_tool(*, input: Int, messages: list[Message]) -> Invoke:
        return Invoke(action="tool", rationale="", value=input.value * 2)  # type: ignore

    assert isinstance(static_tool, Tool)
    assert static_tool.name == "static_tool"
    assert static_tool.description == """This is a static Tool"""
    output = static_tool(input=Str(value="5"), messages=[])
    assert isinstance(output, Str)

    assert isinstance(dynamic_tool, Tool)
    assert dynamic_tool.name == "dynamic_tool"
    output = dynamic_tool(input=Int(value=5), messages=[])
    assert isinstance(output, Invoke)


def test_tool_description_normalizes_multiline_docstring():
    @tool
    def described_tool(input: Str, messages: list[Message]) -> Str:
        """Use the requested value.

        Preserve meaningful paragraph breaks without source indentation.
        """
        return input

    assert described_tool.description == (
        "Use the requested value.\n\n"
        "Preserve meaningful paragraph breaks without source indentation."
    )


def test_factory_description_normalizes_multiline_docstring():

    @factory
    def described_factory(input: Str, messages: list[Message], ctx: Ctx) -> Str:
        """Use the configured operation.

        Return its value without source indentation.
        """
        return input

    assert described_factory.description == (
        "Use the configured operation.\n\nReturn its value without source indentation."
    )
    assert described_factory(Ctx()).description == described_factory.description


def test_tool_invalid_input():
    with pytest.raises(ValueError, match="input must be subclass"):

        @tool
        def invalid_input_tool(*, input: Invalid, messages: list[Message]) -> Empty: ...


def test_tool_invalid_output():
    with pytest.raises(ValueError, match="output must be subclass"):

        @tool
        def invalid_output_tool(
            *, input: Empty, messages: list[Message]
        ) -> Invalid: ...


def test_chain_appends_targets_without_overwriting_existing_links():
    @tool
    def parent_a(input: Str, messages: list[Message]) -> Str:
        return input

    @tool
    def parent_b(input: Str, messages: list[Message]) -> Str:
        return input

    @tool(chained_to=parent_a)
    def child(input: Str, messages: list[Message]) -> Str:
        return input

    child.chain(parent_b)

    assert child.chained_to is not None
    assert len(child.chained_to) == 2
    assert child.chained_to[0].id == parent_a.id
    assert child.chained_to[1].id == parent_b.id


def test_chain_keeps_existing_condition_when_no_new_condition_given():
    @tool
    def parent_a(input: Str, messages: list[Message]) -> Str:
        return input

    @tool
    def parent_b(input: Str, messages: list[Message]) -> Str:
        return input

    @tool(chained_to=parent_a, chain_condition=lambda x: x.value == "ok")
    def child(input: Str, messages: list[Message]) -> Str:
        return input

    child.chain(parent_b)

    assert child.chain_condition(Str(value="ok")) is True
    assert child.chain_condition(Str(value="nope")) is False


def test_tool_to_tool_list_flattens_nested_and_handles_none() -> None:
    @tool
    def t_a(input: Str, messages: list[Message]) -> Str:
        return input

    @tool
    def t_b(input: Str, messages: list[Message]) -> Str:
        return input

    assert Tool.to_tool_list(None) == []
    assert Tool.to_tool_list([t_a, [t_b], t_a]) == [t_a, t_b, t_a]
