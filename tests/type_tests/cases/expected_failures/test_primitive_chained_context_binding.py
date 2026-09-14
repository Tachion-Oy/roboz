"""Chaining preserves the child's concrete context requirement."""

from roboz.models import Message, Str
from roboz import factory, tool
from roboz.dependencies import ExecutableDependency
from tests.type_tests.fixtures.primitive_contexts import ProgramContext


@tool
def echo(input: Str, messages: list[Message]) -> Str:
    return input


@factory(chained_to=echo)
def describe_value(input: Str, messages: list[Message], ctx: ProgramContext) -> Str:
    return Str(value=ctx.prefix + input.value)


# Expected: reportArgumentType; the child requires ProgramContext.
describe_value(ExecutableDependency("python"))
