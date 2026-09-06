from typing import assert_type

from roboz import Ctx, ExecutableDependency, Factory, Message, Str, Tool, factory


@factory
def add_prefix(input: Str, messages: list[Message], ctx: Ctx) -> Str:
    return Str(value=f"{ctx.prefix}{input.value}")


assert_type(add_prefix, Factory[Str, Str, Ctx])
assert_type(add_prefix(Ctx(prefix="hello")), Tool[Str, Str])
assert_type(Ctx(converter=ExecutableDependency("python")), Ctx)
