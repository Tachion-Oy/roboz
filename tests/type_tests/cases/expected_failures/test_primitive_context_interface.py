"""A factory's context must provide the structural inspection contract."""

from dataclasses import dataclass

from roboz import Message, Str, factory


@dataclass
class MissingInspection:
    prefix: str = ""


# Expected: decorator type error; MissingInspection does not satisfy Context.
@factory
def describe_value(input: Str, messages: list[Message], ctx: MissingInspection) -> Str:
    return Str(value=ctx.prefix + input.value)
