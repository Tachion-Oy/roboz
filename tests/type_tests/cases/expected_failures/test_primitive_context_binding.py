"""An inspectable object still must match the factory's concrete context type."""

from roboz.models import Message, Str
from roboz import factory
from roboz.dependencies import ExecutableDependency
from tests.type_tests.fixtures.primitive_contexts import ProgramContext


@factory
def describe_program(
    input: Str, messages: list[Message], ctx: ExecutableDependency
) -> Str:
    return Str(value=ctx.executable)


# Expected: reportArgumentType; ProgramContext is not ExecutableDependency.
describe_program(ProgramContext(executable=ExecutableDependency("python")))
