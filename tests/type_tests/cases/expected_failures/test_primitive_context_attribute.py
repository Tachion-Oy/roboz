"""Context fields do not fall back to Any."""

from roboz.models import Message, Str
from roboz import factory
from tests.type_tests.fixtures.primitive_contexts import ProgramContext


@factory()
def describe_program(input: Str, messages: list[Message], ctx: ProgramContext) -> Str:
    # Expected: reportAttributeAccessIssue; misspelled field is not declared.
    return Str(value=ctx.prefxi)
