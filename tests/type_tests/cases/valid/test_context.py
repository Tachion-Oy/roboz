from typing import assert_type

from roboz import (
    Ctx,
    ExecutableDependency,
    ExternalDependency,
    ExternalDependencySource,
    Factory,
    Message,
    Str,
    Tool,
    factory,
)


@factory
def add_prefix(input: Str, messages: list[Message], ctx: Ctx) -> Str:
    return Str(value=f"{ctx.prefix}{input.value}")


assert_type(add_prefix, Factory[Str, Str, Ctx])
assert_type(add_prefix(Ctx(prefix="hello")), Tool[Str, Str])
assert_type(Ctx(converter=ExecutableDependency("python")), Ctx)


ctx = Ctx(converter=ExecutableDependency("python"))
assert_type(ctx.external_dependencies(), tuple[ExternalDependency, ...])


def inspect_source(source: ExternalDependencySource) -> tuple[ExternalDependency, ...]:
    return source.external_dependencies()


assert_type(inspect_source(ctx), tuple[ExternalDependency, ...])
