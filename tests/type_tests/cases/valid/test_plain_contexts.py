"""Plain classes and lists are valid contexts without resource inspection."""

from typing import assert_type

from roboz import Factory, Tool, factory
from roboz.models import Message, Str
from tests.type_tests.fixtures.primitive_contexts import PrefixContext


@factory
def add_prefix(input: Str, messages: list[Message], ctx: PrefixContext) -> Str:
    return Str(value=ctx.prefix + input.value)


@factory()
def remember_value(input: Str, messages: list[Message], ctx: list[str]) -> Str:
    ctx.append(input.value)
    return Str(value=", ".join(ctx))


@factory(chained_to=add_prefix)
def remember_prefixed_value(
    input: Str, messages: list[Message], ctx: list[str]
) -> Str:
    ctx.append(input.value)
    return input


assert_type(add_prefix, Factory[Str, Str, PrefixContext])
assert_type(remember_value, Factory[Str, Str, list[str]])
assert_type(remember_prefixed_value, Factory[Str, Str, list[str]])
assert_type(add_prefix(PrefixContext(prefix="> ")), Tool[Str, Str])
history: list[str] = []
assert_type(remember_value(history), Tool[Str, Str])
assert_type(remember_prefixed_value(history), Tool[Str, Str])
