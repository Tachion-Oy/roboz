"""Parenthesized factories retain the concrete context requirement."""

from roboz import Message, Str, factory
from tests.type_tests.fixtures.primitive_contexts import PrefixContext


@factory()
def add_prefix(input: Str, messages: list[Message], ctx: PrefixContext) -> Str:
    return Str(value=ctx.prefix + input.value)


values: dict[str, str] = {"prefix": "> "}
# Expected: reportArgumentType; the context must be PrefixContext.
add_prefix(values)
