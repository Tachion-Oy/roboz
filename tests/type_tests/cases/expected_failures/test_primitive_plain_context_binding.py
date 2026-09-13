"""Plain contexts still require the concrete class declared by the factory."""

from roboz import Message, Str, factory
from roboz.dependencies import ExecutableDependency
from tests.type_tests.fixtures.primitive_contexts import PrefixContext, ProgramContext


@factory
def add_prefix(input: Str, messages: list[Message], ctx: PrefixContext) -> Str:
    return Str(value=ctx.prefix + input.value)


# Both classes have prefix, but ProgramContext is not the declared PrefixContext.
# Expected: reportArgumentType.
add_prefix(ProgramContext(executable=ExecutableDependency("python")))
