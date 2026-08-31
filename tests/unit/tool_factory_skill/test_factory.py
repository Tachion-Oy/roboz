from dataclasses import dataclass

import pytest
from pydantic import BaseModel

from roboz.models import Empty, Int, Invoke, Message, Str
from roboz.tooling.decorators import factory, tool
from roboz.tooling.dependencies import FactoryCtx
from roboz.tooling.core import Tool


class Invalid(BaseModel): ...


def test_create_factory():
    @dataclass(frozen=True)
    class Context(FactoryCtx):
        multiplier: int = 2

    @factory
    def static_factory(input: Str, messages: list[Message], ctx: Context) -> Int:
        return Int(value=int(input.value) * ctx.multiplier)

    @factory
    def dynamic_factory(input: Int, messages: list[Message], ctx: Context) -> Invoke:
        res: dict = dict(
            action="tool", rationale="", value=int(input.value) * ctx.multiplier
        )
        return Invoke(**res)

    ctx = Context()

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
    @dataclass(frozen=True)
    class Context(FactoryCtx): ...

    @factory
    def invalid_input_factory(
        *, input: Invalid, messages: list[Message], ctx: Context
    ) -> Empty: ...

    with pytest.raises(ValueError, match="input must be subclass"):
        invalid_input_factory(Context())


def test_factory_invalid_output():
    @dataclass(frozen=True)
    class Context(FactoryCtx): ...

    @factory
    def invalid_output_factory(
        *, input: Empty, messages: list[Message], ctx: Context
    ) -> Invalid: ...

    with pytest.raises(ValueError, match="output must be subclass"):
        invalid_output_factory(Context())


def test_factory_chain_valid():
    @dataclass(frozen=True)
    class Context(FactoryCtx): ...

    @tool
    def root_tool(*, input: Str, messages: list[Message]) -> Str:
        return Str(value=input.value)

    @factory(chained_to=root_tool)
    def next_factory(*, input: Str, messages: list[Message], ctx: Context) -> Str:
        return Str(value=str(int(input.value) + 1))

    next_tool = next_factory(Context())
    assert next_tool.chained_to == [root_tool]

    # Should not fail according to Liskov
    @factory(chained_to=root_tool)
    def compatible_factory(
        *, input: Empty, messages: list[Message], ctx: Context
    ) -> Str: ...


def test_factory_chain_invalid_type():
    @dataclass(frozen=True)
    class Context(FactoryCtx): ...

    @tool
    def root_tool(*, input: Str, messages: list[Message]) -> Str | Int:
        return Str(value=input.value)

    with pytest.raises(ValueError):

        @factory(chained_to=root_tool)
        def invalid_root_factory(
            *, input: Invalid, messages: list[Message], ctx: Context
        ) -> Str: ...

        invalid_root_factory(Context())

    @tool
    def dynamic_tool(*, input: Str, messages: list[Message]) -> Invoke:
        return Invoke(action="", rationale="")

    with pytest.raises(ValueError):

        @factory(chained_to=dynamic_tool)
        def invalid_dynamic_factory(
            *, input: Invoke, messages: list[Message], ctx: Context
        ) -> Str: ...

        invalid_dynamic_factory(Context())


def test_factory_chain_list_valid():
    @dataclass(frozen=True)
    class Context(FactoryCtx): ...

    @tool
    def valid_tool(*, input: Str, messages: list[Message]) -> Str | Int:
        return Str(value=input.value)

    @factory(chained_to=[valid_tool, valid_tool])
    def next_factory(*, input: Str, messages: list[Message], ctx: Context) -> Str:
        return Str(value=str(1))

    valid_tool_copy = valid_tool.copy()

    @factory(chained_to=[valid_tool_copy, valid_tool_copy])
    def next_factory_with_copy(
        *, input: Str, messages: list[Message], ctx: Context
    ) -> Str:
        return Str(value=str(1))

    next_tool = next_factory(Context())
    if next_tool.chained_to:
        assert len(next_tool.chained_to) == 2
