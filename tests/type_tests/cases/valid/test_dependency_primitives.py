"""Concrete context inference for each supported factory decorator shape."""

from typing import assert_type

from roboz.tooling import HasExternalDependencies
from roboz import Factory, Tool, factory, tool
from roboz.models import Int, Message, Str
from roboz.dependencies import ExecutableDependency, ExternalDependency
from tests.type_tests.fixtures.primitive_contexts import ProgramContext


@factory
def describe_program(
    input: Str, messages: list[Message], ctx: ExecutableDependency
) -> Str:
    return Str(value=f"{ctx.executable}: {input.value}")


@factory()
def describe_configured_program(
    input: Str, messages: list[Message], ctx: ProgramContext
) -> Str:
    assert_type(ctx.executable, ExecutableDependency)
    assert_type(ctx.prefix, str)
    return Str(value=f"{ctx.prefix}{ctx.executable.executable}: {input.value}")


assert_type(describe_program, Factory[Str, Str, ExecutableDependency])
assert_type(describe_configured_program, Factory[Str, Str, ProgramContext])

program = ExecutableDependency("python")
configuration = ProgramContext(executable=program, prefix="> ")
assert_type(describe_program(program), Tool[Str, Str])
assert_type(describe_program(program).copy(), Tool[Str, Str])
assert_type(describe_configured_program(configuration), Tool[Str, Str])
assert_type(program.external_dependencies(), tuple[ExternalDependency, ...])
assert_type(
    describe_program(program).external_dependencies(), tuple[ExternalDependency, ...]
)


def inspect_context(ctx: HasExternalDependencies) -> tuple[ExternalDependency, ...]:
    return ctx.external_dependencies()


# Both implement the protocol without inheriting HasExternalDependencies.
assert_type(inspect_context(program), tuple[ExternalDependency, ...])
assert_type(inspect_context(configuration), tuple[ExternalDependency, ...])


@tool
def echo(input: Str, messages: list[Message]) -> Str:
    return input


@factory(chained_to=echo)
def after_tool(input: Str, messages: list[Message], ctx: ProgramContext) -> Int:
    return Int(value=len(ctx.prefix + input.value))


@factory(chained_to=describe_program)
def after_factory(input: Str, messages: list[Message], ctx: ProgramContext) -> Int:
    return Int(value=len(ctx.prefix + input.value))


assert_type(after_tool, Factory[Str, Int, ProgramContext])
assert_type(after_factory, Factory[Str, Int, ProgramContext])
assert_type(after_tool(configuration), Tool[Str, Int])
assert_type(after_factory(configuration), Tool[Str, Int])


@factory(chained_to=[echo, describe_program])
def after_sequence(input: Str, messages: list[Message], ctx: ProgramContext) -> Str:
    return Str(value=ctx.prefix + input.value)


assert_type(after_sequence, Factory[Str, Str, ProgramContext])
assert_type(after_sequence(configuration), Tool[Str, Str])


@tool
def choose_value(input: Str, messages: list[Message]) -> Str | Int:
    return input


@factory
def choose_configured_value(
    input: Str, messages: list[Message], ctx: ExecutableDependency
) -> Str | Int:
    return input


@factory(
    chained_to=choose_value, chain_condition=lambda output: isinstance(output, Str)
)
def after_union_tool(
    input: Str, messages: list[Message], ctx: ExecutableDependency
) -> Str:
    return Str(value=f"{ctx.executable}: {input.value}")


@factory(
    chained_to=choose_configured_value,
    chain_condition=lambda output: isinstance(output, Str),
)
def after_union_factory(
    input: Str, messages: list[Message], ctx: ProgramContext
) -> Str:
    return Str(value=ctx.prefix + input.value)


@factory(
    chained_to=[choose_value, choose_configured_value],
    chain_condition=lambda output: isinstance(output, Str),
)
def after_union_sequence(
    input: Str, messages: list[Message], ctx: ProgramContext
) -> Str:
    return Str(value=ctx.prefix + input.value)


# Preserve the existing union-input inference while keeping the exact context.
assert_type(
    after_union_tool,
    Factory[Str, Str, ExecutableDependency] | Factory[Int, Str, ExecutableDependency],
)
assert_type(
    after_union_factory,
    Factory[Str, Str, ProgramContext] | Factory[Int, Str, ProgramContext],
)
assert_type(
    after_union_sequence,
    Factory[Str, Str, ProgramContext] | Factory[Int, Str, ProgramContext],
)


# Direct decorator calls exercise the callable-taking overloads too.


def describe_value(input: Str, messages: list[Message], ctx: ProgramContext) -> Str:
    return Str(value=ctx.prefix + input.value)


assert_type(factory(describe_value, chained_to=echo), Factory[Str, Str, ProgramContext])
assert_type(
    factory(describe_value, chained_to=[echo, describe_program]),
    Factory[Str, Str, ProgramContext],
)
assert_type(
    factory(
        describe_value,
        chained_to=choose_value,
        chain_condition=lambda output: isinstance(output, Str),
    ),
    Factory[Str, Str, ProgramContext],
)
