import pytest
from pydantic import BaseModel

from roboz.models import Empty, Int, Invoke, Message, Str
from roboz.tooling.core import Tool
from roboz.tooling.decorators import factory, tool


class Invalid(BaseModel): ...


def test_create_factory():

    @factory
    def static_factory(input: Str, messages: list[Message], ctx: int) -> Int:
        return Int(value=int(input.value) * ctx)

    @factory
    def dynamic_factory(input: Int, messages: list[Message], ctx: int) -> Invoke:
        res: dict = dict(action="tool", rationale="", value=int(input.value) * ctx)
        return Invoke(**res)

    ctx = 2

    static_tool = static_factory(ctx)
    assert isinstance(static_tool, Tool)
    assert static_tool.name == "static_factory"
    output = static_tool(input=Str(value="5"), messages=[])
    assert isinstance(output, Int)
    assert output.value == 10

    dynamic_tool = dynamic_factory(ctx)
    assert isinstance(dynamic_tool, Tool)
    assert dynamic_tool.name == "dynamic_factory"
    output = dynamic_tool(input=Int(value=5), messages=[])
    assert isinstance(output, Invoke)
    assert output.value == 10  # type: ignore


def test_factory_invalid_input():

    @factory
    def invalid_input_factory(
        *, input: Invalid, messages: list[Message], ctx: int
    ) -> Empty: ...

    with pytest.raises(ValueError, match="input must be subclass"):
        invalid_input_factory(1)


def test_factory_invalid_output():

    @factory
    def invalid_output_factory(
        *, input: Empty, messages: list[Message], ctx: int
    ) -> Invalid: ...

    with pytest.raises(ValueError, match="output must be subclass"):
        invalid_output_factory(1)


def test_factory_chain_valid():

    @tool
    def root_tool(*, input: Str, messages: list[Message]) -> Str:
        return Str(value=input.value)

    @factory(chained_to=root_tool)
    def next_factory(*, input: Str, messages: list[Message], ctx: int) -> Str:
        return Str(value=str(int(input.value) + 1))

    next_tool = next_factory(1)
    assert next_tool.chained_to == [root_tool]

    # Should not fail according to Liskov
    @factory(chained_to=root_tool)
    def compatible_factory(
        *, input: Empty, messages: list[Message], ctx: int
    ) -> Str: ...


def test_factory_chain_invalid_type():

    @tool
    def root_tool(*, input: Str, messages: list[Message]) -> Str | Int:
        return Str(value=input.value)

    with pytest.raises(ValueError):

        @factory(chained_to=root_tool)
        def invalid_root_factory(
            *, input: Invalid, messages: list[Message], ctx: int
        ) -> Str: ...

        invalid_root_factory(1)

    @tool
    def dynamic_tool(*, input: Str, messages: list[Message]) -> Invoke:
        return Invoke(action="", rationale="")

    with pytest.raises(ValueError):

        @factory(chained_to=dynamic_tool)
        def invalid_dynamic_factory(
            *, input: Invoke, messages: list[Message], ctx: int
        ) -> Str: ...

        invalid_dynamic_factory(1)


def test_factory_chain_list_valid():

    @tool
    def valid_tool(*, input: Str, messages: list[Message]) -> Str | Int:
        return Str(value=input.value)

    @factory(chained_to=[valid_tool, valid_tool])
    def next_factory(*, input: Str, messages: list[Message], ctx: int) -> Str:
        return Str(value=str(1))

    valid_tool_copy = valid_tool.copy()

    @factory(chained_to=[valid_tool_copy, valid_tool_copy])
    def next_factory_with_copy(*, input: Str, messages: list[Message], ctx: int) -> Str:
        return Str(value=str(1))

    next_tool = next_factory(1)
    if next_tool.chained_to:
        assert len(next_tool.chained_to) == 2
